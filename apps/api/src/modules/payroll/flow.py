"""Draft recalculation, loan movements, bank returns, and bulk files."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select

from src.core.exceptions import AppError, NotFoundError
from src.db.models import (
    Employee,
    EmployeeBankAccount,
    EmployeeSalaryAssignment,
    EmployeeStatutoryProfile,
    PayrollArrear,
    PayrollLoan,
    PayrollLoanEvent,
    PayrollPayment,
    PayrollPayslip,
    PayrollPayslipLine,
    PayrollReimbursement,
    SalaryComponent,
    SalaryTemplateLine,
)
from src.modules.payroll.calc import ComponentDef
from src.modules.payroll.ledger import post_salary_payment
from src.modules.payroll.ops import (
    arrears_for_revision,
    backdated_months,
    emi_schedule,
    monthly_cash,
    parse_bank_response,
    pf_ecr_text,
    read_csv,
    rows_csv,
    rows_xlsx,
    validate_bank_row,
    validate_employee_row,
)
from src.modules.payroll.permissions import PREPARE, SETTINGS_WRITE
from src.modules.payroll.text import check_pan

_ZERO = Decimal("0.00")


def _money(value) -> Decimal:
    return Decimal(str(value if value is not None else 0)).quantize(Decimal("0.01"))


class PayrollFlow:
    async def _fill_statutory(self, run, period_start: date, period_end: date) -> list[str]:
        employees = list(
            (
                await self.db.execute(
                    select(Employee).where(Employee.school_id == self.school_id, Employee.status == "ACTIVE")
                )
            ).scalars()
        )
        skipped = []
        for employee in employees:
            assignment = await self._assignment(employee.id, period_end)
            if assignment is None:
                skipped.append(str(employee.id))
                continue
            pay, loan_rows = await self._compute_employee(employee.id, period_start, period_end, persist_loans=True)
            slip = PayrollPayslip(
                id=uuid4(),
                school_id=self.school_id,
                payroll_run_id=run.id,
                teacher_id=employee.teacher_id,
                employee_id=employee.id,
                gross_amount=pay.gross,
                deductions_amount=pay.deductions,
                net_amount=pay.net,
                status="DRAFT",
                working_days=pay.working_days,
                lop_days=pay.lop_days,
                paid_days=pay.paid_days,
                tax_regime=pay.tds.regime if pay.tds else None,
                form16=pay.form16,
                lop_detail=list(pay.lop_detail),
            )
            self.db.add(slip)
            for line in pay.lines:
                self.db.add(
                    PayrollPayslipLine(
                        id=uuid4(),
                        school_id=self.school_id,
                        payslip_id=slip.id,
                        code=line.code,
                        name=line.name,
                        kind=line.kind,
                        amount=line.amount,
                        display_order=line.display_order,
                    )
                )
            self.db.add(
                PayrollPayment(
                    id=uuid4(),
                    school_id=self.school_id,
                    payslip_id=slip.id,
                    amount=pay.net,
                    status="PENDING",
                )
            )
            await self._consume_inputs(employee.id, slip.id, loan_rows, pay)
        return skipped

    async def _restore_slip_inputs(self, slip: PayrollPayslip) -> None:
        arrears = (
            await self.db.execute(select(PayrollArrear).where(PayrollArrear.payslip_id == slip.id))
        ).scalars()
        for row in arrears:
            row.status = "PENDING"
            row.payslip_id = None
        claims = (
            await self.db.execute(select(PayrollReimbursement).where(PayrollReimbursement.payslip_id == slip.id))
        ).scalars()
        for row in claims:
            if row.status == "PAID":
                row.status = "APPROVED"
            row.payslip_id = None
        events = list(
            (
                await self.db.execute(
                    select(PayrollLoanEvent).where(
                        PayrollLoanEvent.payslip_id == slip.id, PayrollLoanEvent.kind == "EMI"
                    )
                )
            ).scalars()
        )
        for event in events:
            loan = await self.db.get(PayrollLoan, event.loan_id)
            if loan is not None and loan.school_id == self.school_id:
                loan.balance = _money(loan.balance + event.principal)
                if loan.status == "CLOSED" and loan.balance > 0:
                    loan.status = "ACTIVE"
            await self.db.delete(event)

    async def recalculate_run(self, run_id: UUID, reason: str | None) -> dict:
        await self._allow(PREPARE)
        run = await self._run(run_id)
        if run.approval_status not in {"DRAFT", "REJECTED"}:
            raise AppError("Only a draft or rejected run can be recalculated", status_code=422)
        slips = list(
            (
                await self.db.execute(select(PayrollPayslip).where(PayrollPayslip.payroll_run_id == run.id))
            ).scalars()
        )
        for slip in slips:
            await self._restore_slip_inputs(slip)
            await self.db.execute(delete(PayrollPayslipLine).where(PayrollPayslipLine.payslip_id == slip.id))
            await self.db.execute(delete(PayrollPayment).where(PayrollPayment.payslip_id == slip.id))
            await self.db.delete(slip)
        await self.db.flush()
        skipped = await self._fill_statutory(run, run.period_start, run.period_end)
        run.approval_status = "DRAFT"
        run.rejection_reason = None
        await self._audit("run.recalculate", "payroll_run", run.id, None, {"skipped": skipped}, reason)
        await self.db.flush()
        return {"run": run, "created": False, "skipped": skipped, "recalculated": True}

    async def cancel_slips(self, run) -> None:
        slips = list(
            (await self.db.execute(select(PayrollPayslip).where(PayrollPayslip.payroll_run_id == run.id))).scalars()
        )
        for slip in slips:
            if slip.status == "CANCELLED":
                continue
            await self._restore_slip_inputs(slip)
            slip.status = "CANCELLED"

    async def _sync_paid(self, run_id: UUID) -> None:
        run = await self._run(run_id)
        if run.approval_status not in {"PUBLISHED", "APPROVED", "PAID"}:
            return
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip).where(
                        PayrollPayslip.payroll_run_id == run.id, PayrollPayslip.status != "CANCELLED"
                    )
                )
            ).scalars()
        )
        if slips and all(slip.status == "PAID" for slip in slips):
            run.approval_status = "PAID"

    async def _loan(self, loan_id: UUID) -> PayrollLoan:
        loan = await self.db.get(PayrollLoan, loan_id)
        if loan is None or loan.school_id != self.school_id:
            raise NotFoundError("Loan not found")
        return loan

    async def loan_schedule(self, loan_id: UUID) -> dict:
        await self._allow(PREPARE)
        loan = await self._loan(loan_id)
        return {
            "loan_id": str(loan.id),
            "balance": str(loan.balance),
            "emi": str(loan.emi),
            "annual_rate": str(loan.annual_rate),
            "status": loan.status,
            "schedule": emi_schedule(
                balance=loan.balance, emi=loan.emi, annual_rate=loan.annual_rate, start=date.today()
            ),
        }

    async def prepay_loan(self, loan_id: UUID, amount: Decimal, reason: str) -> PayrollLoan:
        await self._allow(PREPARE)
        loan = await self._loan(loan_id)
        if loan.status != "ACTIVE":
            raise AppError("Only an active loan can take a prepayment", status_code=422)
        paid = _money(amount)
        if paid <= 0:
            raise AppError("Prepayment must be greater than zero", status_code=422)
        if paid > loan.balance:
            raise AppError("Prepayment cannot be more than the balance", status_code=422)
        loan.balance = _money(loan.balance - paid)
        if loan.balance <= 0:
            loan.balance = _ZERO
            loan.status = "CLOSED"
        self.db.add(
            PayrollLoanEvent(
                id=uuid4(),
                school_id=self.school_id,
                loan_id=loan.id,
                kind="CLOSE" if loan.status == "CLOSED" else "PREPAY",
                principal=paid,
                interest=_ZERO,
                balance_after=loan.balance,
                note=reason,
            )
        )
        await self._audit("loan.prepay", "payroll_loan", loan.id, None, {"balance": str(loan.balance)}, reason)
        await self.db.flush()
        return loan

    async def close_loan(self, loan_id: UUID, reason: str) -> PayrollLoan:
        await self._allow(PREPARE)
        loan = await self._loan(loan_id)
        if loan.status == "CLOSED":
            return loan
        principal = loan.balance
        loan.balance = _ZERO
        loan.status = "CLOSED"
        self.db.add(
            PayrollLoanEvent(
                id=uuid4(),
                school_id=self.school_id,
                loan_id=loan.id,
                kind="CLOSE",
                principal=principal,
                interest=_ZERO,
                balance_after=_ZERO,
                note=reason,
            )
        )
        await self._audit("loan.close", "payroll_loan", loan.id, None, {"status": "CLOSED"}, reason)
        await self.db.flush()
        return loan

    async def _template_cash(self, template_id: UUID, overrides: dict) -> Decimal:
        lines = list(
            (
                await self.db.execute(select(SalaryTemplateLine).where(SalaryTemplateLine.template_id == template_id))
            ).scalars()
        )
        resolved = []
        for line in lines:
            component = await self.db.get(SalaryComponent, line.component_id)
            if component is None or component.effective_to is not None:
                continue
            amount = line.amount if line.amount is not None else component.amount
            percent = line.percent if line.percent is not None else component.percent
            raw = (overrides or {}).get(component.code)
            if isinstance(raw, dict):
                if raw.get("amount") is not None:
                    amount = _money(raw["amount"])
                if raw.get("percent") is not None:
                    percent = _money(raw["percent"])
            elif raw is not None:
                if component.calc_type == "PERCENT":
                    percent = _money(raw)
                else:
                    amount = _money(raw)
            resolved.append(
                ComponentDef(
                    code=component.code,
                    name=component.name,
                    kind=component.kind,
                    calc_type=component.calc_type,
                    amount=amount or _ZERO,
                    percent=percent,
                    percent_of=component.percent_of,
                )
            )
        return monthly_cash(resolved)

    async def revise_salary(self, data: dict) -> EmployeeSalaryAssignment:
        await self._allow(SETTINGS_WRITE)
        employee = await self._employee(data["employee_id"])
        start = data["effective_from"]
        previous = (
            await self.db.execute(
                select(EmployeeSalaryAssignment)
                .where(
                    EmployeeSalaryAssignment.employee_id == employee.id,
                    EmployeeSalaryAssignment.effective_to.is_(None),
                )
                .order_by(EmployeeSalaryAssignment.effective_from.desc())
            )
        ).scalars().first()
        old_monthly = monthly_cash(await self._resolved_components(previous)) if previous else _ZERO
        new_monthly = await self._template_cash(data["template_id"], data.get("overrides") or {})
        months = backdated_months(start, date.today())
        computed = arrears_for_revision(old_monthly=old_monthly, new_monthly=new_monthly, months=months)
        payload = dict(data)
        if data.get("compute_arrears", True) and computed > 0 and not data.get("arrears_amount"):
            payload["arrears_amount"] = computed
        row = await self.assign_salary(payload)
        await self._audit(
            "salary.revise",
            "employee_salary",
            row.id,
            {"old_monthly": str(old_monthly)},
            {"new_monthly": str(new_monthly), "arrears": str(payload.get("arrears_amount") or 0), "months": months},
            data.get("reason"),
        )
        return row

    async def payslips_zip(self, run_id: UUID) -> tuple[bytes, str]:
        await self._allow(PREPARE)
        run = await self._run(run_id)
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip).where(
                        PayrollPayslip.payroll_run_id == run.id, PayrollPayslip.status != "CANCELLED"
                    )
                )
            ).scalars()
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for slip in slips:
                pdf, name = await self.payslip_pdf(slip.id, intent="download")
                archive.writestr(name, pdf)
        await self._audit("payslip.zip", "payroll_run", run.id, None, {"count": len(slips)}, None)
        return buffer.getvalue(), f"payslips-{run.id}.zip"

    async def pf_ecr(self, run_id: UUID) -> tuple[str, str]:
        await self._allow(PREPARE)
        detail = await self.get_run(run_id)
        rows = []
        for item in detail["payslips"]:
            slip = item["payslip"]
            if slip.status == "CANCELLED":
                continue
            employee = await self.db.get(Employee, slip.employee_id) if slip.employee_id else None
            profile = None
            if employee:
                profile = (
                    await self.db.execute(
                        select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
                    )
                ).scalar_one_or_none()
            amounts = {line.code: line.amount for line in item["lines"]}
            name = ""
            if employee:
                name = employee.full_name or employee.employee_code or ""
            rows.append(
                {
                    "uan": profile.uan if profile else "",
                    "name": name,
                    "gross": slip.gross_amount,
                    "epf_wages": amounts.get("BASIC", _ZERO),
                    "eps_wages": amounts.get("BASIC", _ZERO),
                    "edli_wages": amounts.get("BASIC", _ZERO),
                    "employee_share": amounts.get("PF", _ZERO),
                    "eps": amounts.get("EPS", _ZERO),
                    "ncp_days": slip.lop_days or 0,
                }
            )
        return pf_ecr_text(rows), "text/plain"

    async def register_file(self, run_id: UUID, kind: str) -> tuple[bytes | str, str, str]:
        rows = await self.salary_register(run_id)
        headers = ["employee_id", "gross", "deductions", "net", "lop_days"]
        table = [[row["employee_id"] or "", row["gross"], row["deductions"], row["net"], row["lop_days"]] for row in rows]
        if kind == "xlsx":
            return rows_xlsx(headers, table), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "register.xlsx"
        return rows_csv(headers, table), "text/csv", "register.csv"

    async def export_table(self, kind: str, file_format: str) -> tuple[bytes | str, str, str]:
        await self._allow(PREPARE)
        if kind == "loans":
            headers = ["id", "employee_id", "principal", "balance", "emi", "annual_rate", "status"]
            table = [
                [str(row.id), str(row.employee_id), str(row.principal), str(row.balance), str(row.emi), str(row.annual_rate), row.status]
                for row in await self.list_loans(None)
            ]
        elif kind == "arrears":
            headers = ["id", "employee_id", "amount", "status", "reason"]
            table = [
                [str(row.id), str(row.employee_id), str(row.amount), row.status, row.reason]
                for row in await self.list_arrears(None, None)
            ]
        elif kind == "reimbursements":
            headers = ["id", "employee_id", "amount", "status", "note"]
            table = [
                [str(row.id), str(row.employee_id), str(row.amount), row.status, row.note]
                for row in await self.list_reimbursements(None, None)
            ]
        else:
            raise AppError("Unknown export", status_code=422)
        filename = f"{kind}.{file_format}"
        if file_format == "xlsx":
            return (
                rows_xlsx(headers, table),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename,
            )
        return rows_csv(headers, table), "text/csv", filename

    async def import_employees(self, content: bytes) -> dict:
        await self._allow(SETTINGS_WRITE)
        try:
            _headers, rows = read_csv(content)
        except ValueError as exc:
            raise AppError(str(exc), status_code=422) from exc
        created = 0
        updated = 0
        errors = []
        for index, row in enumerate(rows, start=2):
            row_errors = validate_employee_row(row, index)
            if row_errors:
                errors.extend(row_errors)
                continue
            code = row["employee_code"]
            existing = (
                await self.db.execute(
                    select(Employee).where(
                        Employee.school_id == self.school_id,
                        func.lower(Employee.employee_code) == code.lower(),
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                self.db.add(
                    Employee(
                        id=uuid4(),
                        school_id=self.school_id,
                        staff_type="NON_TEACHING",
                        full_name=row["full_name"],
                        email=row.get("email") or None,
                        phone=row.get("phone") or None,
                        employee_code=code,
                        status="ACTIVE",
                    )
                )
                created += 1
                employee_id = None
                await self.db.flush()
                existing = (
                    await self.db.execute(
                        select(Employee).where(
                            Employee.school_id == self.school_id,
                            func.lower(Employee.employee_code) == code.lower(),
                        )
                    )
                ).scalar_one_or_none()
                employee_id = existing.id if existing else None
            else:
                if existing.staff_type != "TEACHING":
                    existing.full_name = row["full_name"]
                if row.get("email"):
                    existing.email = row["email"]
                if row.get("phone"):
                    existing.phone = row["phone"]
                updated += 1
                employee_id = existing.id
            pan = (row.get("pan") or "").strip()
            if pan and employee_id:
                profile = (
                    await self.db.execute(
                        select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee_id)
                    )
                ).scalar_one_or_none()
                if profile is None:
                    profile = EmployeeStatutoryProfile(id=uuid4(), school_id=self.school_id, employee_id=employee_id)
                    self.db.add(profile)
                profile.pan = check_pan(pan)
        await self._audit("import.employees", "employee", None, None, {"created": created, "updated": updated, "errors": len(errors)}, None)
        await self.db.flush()
        return {"created": created, "updated": updated, "errors": errors}

    async def import_banks(self, content: bytes) -> dict:
        await self._allow(PREPARE)
        try:
            _headers, rows = read_csv(content)
        except ValueError as exc:
            raise AppError(str(exc), status_code=422) from exc
        updated = 0
        errors = []
        for index, row in enumerate(rows, start=2):
            parsed, row_errors = validate_bank_row(row, index)
            if row_errors:
                errors.extend(row_errors)
                continue
            employee = (
                await self.db.execute(
                    select(Employee).where(
                        Employee.school_id == self.school_id,
                        func.lower(Employee.employee_code) == parsed["employee_code"].lower(),
                    )
                )
            ).scalar_one_or_none()
            if employee is None:
                errors.append({"row": index, "field": "employee_code", "message": "No employee has this code"})
                continue
            bank = (
                await self.db.execute(
                    select(EmployeeBankAccount).where(
                        EmployeeBankAccount.employee_id == employee.id, EmployeeBankAccount.is_primary.is_(True)
                    )
                )
            ).scalar_one_or_none()
            if bank is None:
                bank = EmployeeBankAccount(
                    id=uuid4(),
                    school_id=self.school_id,
                    employee_id=employee.id,
                    is_primary=True,
                    bank_name=parsed["bank_name"] or "Bank",
                    account_holder=parsed["account_holder"],
                    account_number=parsed["account_number"],
                    ifsc=parsed["ifsc"],
                )
                self.db.add(bank)
            else:
                bank.account_holder = parsed["account_holder"]
                bank.account_number = parsed["account_number"]
                bank.ifsc = parsed["ifsc"]
                if parsed["bank_name"]:
                    bank.bank_name = parsed["bank_name"]
            updated += 1
        await self._audit("import.banks", "employee_bank", None, None, {"updated": updated, "errors": len(errors)}, None)
        await self.db.flush()
        return {"updated": updated, "errors": errors}

    async def import_bank_response(self, run_id: UUID, content: bytes) -> dict:
        await self._allow(PREPARE)
        run = await self._run(run_id)
        if run.approval_status == "LOCKED":
            raise AppError("A locked run cannot be changed", status_code=422)
        if run.approval_status not in {"APPROVED", "PUBLISHED", "PAID"}:
            raise AppError("Import a bank response only after the run is approved", status_code=422)
        parsed, errors = parse_bank_response(content)
        updated = 0
        for item in parsed:
            bank = (
                await self.db.execute(
                    select(EmployeeBankAccount).where(
                        EmployeeBankAccount.school_id == self.school_id,
                        EmployeeBankAccount.account_number == item["account_number"],
                        EmployeeBankAccount.is_primary.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if bank is None:
                errors.append({"row": item["row"], "field": "account_number", "message": "No primary account matches"})
                continue
            slip = (
                await self.db.execute(
                    select(PayrollPayslip).where(
                        PayrollPayslip.payroll_run_id == run.id,
                        PayrollPayslip.employee_id == bank.employee_id,
                        PayrollPayslip.status != "CANCELLED",
                    )
                )
            ).scalar_one_or_none()
            if slip is None:
                errors.append({"row": item["row"], "field": "account_number", "message": "This run has no payslip for that account"})
                continue
            payment = (
                await self.db.execute(select(PayrollPayment).where(PayrollPayment.payslip_id == slip.id))
            ).scalars().first()
            if payment is None or payment.status == "PAID":
                errors.append({"row": item["row"], "field": "status", "message": "That payment is already paid"})
                continue
            payment.status = item["status"]
            if item["status"] == "PAID":
                payment.utr = item["utr"]
                payment.paid_at = datetime.now(timezone.utc)
                payment.failure_reason = None
                slip.status = "PAID"
                slip.paid_on = date.today()
                await post_salary_payment(
                    self.db,
                    school_id=self.school_id,
                    run_id=run.id,
                    amount=payment.amount,
                    paid_on=slip.paid_on,
                    actor_id=self.actor_id,
                    reference=payment.utr,
                )
            else:
                payment.failure_reason = (item.get("reason") or "Bank rejected the payment")[:500]
            updated += 1
        await self._sync_paid(run.id)
        await self._audit("bank.response", "payroll_run", run.id, None, {"updated": updated, "errors": len(errors)}, None)
        await self.db.flush()
        return {"updated": updated, "errors": errors}

    async def retry_failed_payments(self, run_id: UUID, reason: str) -> dict:
        await self._allow(PREPARE)
        run = await self._run(run_id)
        if run.approval_status == "LOCKED":
            raise AppError("A locked run cannot be paid again", status_code=422)
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip).where(
                        PayrollPayslip.payroll_run_id == run.id, PayrollPayslip.status != "CANCELLED"
                    )
                )
            ).scalars()
        )
        retried = 0
        for slip in slips:
            payment = (
                await self.db.execute(select(PayrollPayment).where(PayrollPayment.payslip_id == slip.id))
            ).scalars().first()
            if payment is None or payment.status != "FAILED":
                continue
            payment.status = "PENDING"
            payment.failure_reason = None
            retried += 1
        await self._audit("payment.retry", "payroll_run", run.id, None, {"retried": retried}, reason)
        await self.db.flush()
        return {"retried": retried}
