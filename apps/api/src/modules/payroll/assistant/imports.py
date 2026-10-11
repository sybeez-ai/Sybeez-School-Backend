"""Preview a pay sheet, then import the accepted rows in one transaction."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import (
    Employee,
    EmployeeBankAccount,
    EmployeeStatutoryProfile,
    PayrollAssistantImport,
    PayrollOpeningYtd,
    SalaryComponent,
    SalaryTemplate,
    SalaryTemplateLine,
)
from src.modules.payroll.assistant.llm import assistant_settings, map_headers_with_model
from src.modules.payroll.assistant.mask import mask_payload, redact_text
from src.modules.payroll.assistant.service import confirm_decision
from src.modules.payroll.assistant.sheets import (
    CANONICAL,
    SheetError,
    allow_list,
    deterministic_mapping,
    merge_mapping,
    parse_sheet,
    validate_sheet,
)
from src.modules.payroll.calc import canonical_financial_year, financial_year_label
from src.modules.payroll.permissions import SETTINGS_WRITE
from src.modules.payroll.reports import csv_file
from src.modules.payroll.service import PayrollService
from src.modules.payroll.text import check_pan

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 2000


class PayrollSheetImport:
    def __init__(self, payroll: PayrollService):
        self.payroll = payroll

    async def preview(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str | None,
        financial_year: str | None,
        effective_from: date | None,
    ) -> dict:
        await self.payroll._allow(SETTINGS_WRITE)
        if not content:
            raise AppError("The file is empty", status_code=422)
        if len(content) > MAX_BYTES:
            raise AppError("The file must be 5 MB or smaller", status_code=422)
        fy = _financial_year(financial_year)
        start = effective_from or date.today().replace(day=1)
        try:
            headers, matrix = parse_sheet(content, filename or "sheet.csv")
        except SheetError as exc:
            raise AppError(str(exc), status_code=422) from exc
        if len(matrix) > MAX_ROWS:
            raise AppError("A pay sheet can have at most 2000 rows", status_code=422)
        known = await self._known_codes()
        mapping, unknown = deterministic_mapping(headers, known)
        if unknown:
            proposed = await map_headers_with_model(unknown, sorted(allow_list(known)), assistant_settings())
            mapping = merge_mapping(mapping, proposed, allow_list(known))
        preview = validate_sheet(headers, matrix, mapping, known)
        preview["financial_year"] = fy
        preview["effective_from"] = start.isoformat()
        await self._mark_existing(preview["accepted"])
        row = PayrollAssistantImport(
            id=uuid4(),
            school_id=self.payroll.school_id,
            user_id=self.payroll.actor_id,
            filename=(filename or "sheet")[:255],
            content_type=(content_type or "application/octet-stream")[:120],
            status="PREVIEW",
            mapping=mapping,
            preview=preview,
        )
        self.payroll.db.add(row)
        await self.payroll._audit(
            "assistant.import.preview",
            "payroll_assistant_import",
            row.id,
            None,
            {"filename": row.filename, "accepted": len(preview["accepted"]), "rejected": len({item["row"] for item in preview["rejected"]})},
            None,
        )
        await self.payroll.db.flush()
        return _public(row)

    async def confirm(self, import_id: UUID, idempotency_key: str) -> dict:
        await self.payroll._allow(SETTINGS_WRITE)
        row = await self._owned(import_id)
        owner = (
            await self.payroll.db.execute(
                select(PayrollAssistantImport).where(
                    PayrollAssistantImport.school_id == self.payroll.school_id,
                    PayrollAssistantImport.user_id == self.payroll.actor_id,
                    PayrollAssistantImport.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        decision = confirm_decision(
            status="EXECUTED" if row.status == "IMPORTED" else "PROPOSED",
            stored_key=row.idempotency_key,
            incoming_key=idempotency_key,
            key_owner_is_other=owner is not None and owner.id != row.id,
        )
        if decision == "conflict":
            raise ConflictError("This idempotency key was already used")
        if decision == "replay":
            return {"import_id": str(row.id), "status": "IMPORTED", "result": row.result or {}, "replayed": True}
        result = await self._apply(row)
        row.status = "IMPORTED"
        row.idempotency_key = idempotency_key
        row.result = result
        await self.payroll._audit(
            "assistant.import.confirm",
            "payroll_assistant_import",
            row.id,
            None,
            result,
            None,
        )
        try:
            await self.payroll.db.flush()
        except IntegrityError as exc:
            raise ConflictError("This idempotency key was already used") from exc
        return {"import_id": str(row.id), "status": "IMPORTED", "result": result, "replayed": False}

    async def rejects_csv(self, import_id: UUID) -> str:
        await self.payroll._allow(SETTINGS_WRITE)
        row = await self._owned(import_id)
        rejected = (row.preview or {}).get("rejected") or []
        return csv_file(
            ["row", "employee_code", "field", "reason", "value"],
            [
                [item.get("row"), item.get("employee_code"), item.get("field"), item.get("reason"), redact_text(str(item.get("value") or ""))]
                for item in rejected
            ],
        )

    async def _apply(self, row: PayrollAssistantImport) -> dict:
        preview = row.preview or {}
        accepted = preview.get("accepted") or []
        start = date.fromisoformat(preview["effective_from"])
        fy = preview["financial_year"]
        await self.payroll.ensure_settings()
        components = await self._components()
        needed = sorted({code for item in accepted for code in (item.get("components") or {})})
        missing = [code for code in needed if code not in components]
        if missing:
            raise AppError(f"unknown component: {missing[0]}", status_code=422)
        template = await self._template(row.id, components, needed) if needed else None
        created = 0
        updated = 0
        for item in accepted:
            employee, is_new = await self._employee(item)
            created += int(is_new)
            updated += int(not is_new)
            await self._statutory(employee, item)
            if item.get("bank"):
                await self._bank(employee.id, item)
            if template is not None:
                overrides = {code: {"amount": (item.get("components") or {}).get(code, "0.00")} for code in needed}
                await self.payroll.assign_salary(
                    {
                        "employee_id": employee.id,
                        "template_id": template.id,
                        "effective_from": start,
                        "overrides": overrides,
                        "reason": "Imported pay sheet",
                    }
                )
            await self._ytd(employee.id, fy, item.get("ytd") or {})
        return {
            "created": created,
            "updated": updated,
            "rejected": len({item["row"] for item in (preview.get("rejected") or [])}),
            "assignments": len(accepted) if template is not None else 0,
        }

    async def _owned(self, import_id: UUID) -> PayrollAssistantImport:
        row = await self.payroll.db.get(PayrollAssistantImport, import_id)
        if row is None or row.school_id != self.payroll.school_id or row.user_id != self.payroll.actor_id:
            raise NotFoundError("Import not found")
        return row

    async def _known_codes(self) -> set[str]:
        rows = (
            await self.payroll.db.execute(
                select(SalaryComponent.code).where(
                    SalaryComponent.school_id == self.payroll.school_id,
                    SalaryComponent.effective_to.is_(None),
                )
            )
        ).scalars()
        return {code.upper() for code in rows} | set(CANONICAL)

    async def _components(self) -> dict[str, SalaryComponent]:
        rows = (
            await self.payroll.db.execute(
                select(SalaryComponent).where(
                    SalaryComponent.school_id == self.payroll.school_id,
                    SalaryComponent.effective_to.is_(None),
                )
            )
        ).scalars()
        return {row.code.upper(): row for row in rows}

    async def _mark_existing(self, accepted: list[dict]) -> None:
        codes = [item["employee_code"].lower() for item in accepted]
        if not codes:
            return
        found = {
            code.lower()
            for code in (
                await self.payroll.db.execute(
                    select(Employee.employee_code).where(
                        Employee.school_id == self.payroll.school_id,
                        func.lower(Employee.employee_code).in_(codes),
                    )
                )
            ).scalars()
            if code
        }
        for item in accepted:
            item["action"] = "update" if item["employee_code"].lower() in found else "create"

    async def _employee(self, item: dict) -> tuple[Employee, bool]:
        existing = (
            await self.payroll.db.execute(
                select(Employee).where(
                    Employee.school_id == self.payroll.school_id,
                    func.lower(Employee.employee_code) == item["employee_code"].lower(),
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = Employee(
                id=uuid4(),
                school_id=self.payroll.school_id,
                staff_type="NON_TEACHING",
                full_name=item["full_name"],
                email=item.get("email") or None,
                phone=item.get("phone") or None,
                employee_code=item["employee_code"],
                status="ACTIVE",
            )
            self.payroll.db.add(existing)
            await self.payroll.db.flush()
            return existing, True
        if existing.staff_type != "TEACHING":
            existing.full_name = item["full_name"]
        if item.get("email"):
            existing.email = item["email"]
        if item.get("phone"):
            existing.phone = item["phone"]
        return existing, False

    async def _statutory(self, employee: Employee, item: dict) -> None:
        profile = (
            await self.payroll.db.execute(
                select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
            )
        ).scalar_one_or_none()
        if profile is None:
            profile = EmployeeStatutoryProfile(id=uuid4(), school_id=self.payroll.school_id, employee_id=employee.id)
            self.payroll.db.add(profile)
        profile.pan = check_pan(item["pan"])
        if item.get("uan"):
            profile.uan = item["uan"][:32]
            profile.pf_enrolled = True
        if item.get("pf_number"):
            profile.pf_number = item["pf_number"][:32]
            profile.pf_enrolled = True
        if item.get("esi_number"):
            profile.esi_number = item["esi_number"][:32]
            profile.esi_enrolled = True

    async def _bank(self, employee_id: UUID, item: dict) -> None:
        bank = item["bank"]
        row = (
            await self.payroll.db.execute(
                select(EmployeeBankAccount).where(
                    EmployeeBankAccount.employee_id == employee_id,
                    EmployeeBankAccount.is_primary.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = EmployeeBankAccount(
                id=uuid4(),
                school_id=self.payroll.school_id,
                employee_id=employee_id,
                is_primary=True,
                account_holder=bank["account_holder"],
                account_number=bank["account_number"],
                ifsc=bank["ifsc"],
                bank_name=bank["bank_name"],
            )
            self.payroll.db.add(row)
        else:
            row.account_holder = bank["account_holder"]
            row.account_number = bank["account_number"]
            row.ifsc = bank["ifsc"]
            row.bank_name = bank["bank_name"]
        await self.payroll._audit("bank.update", "bank_account", row.id, None, {"ifsc": row.ifsc}, "Imported pay sheet")

    async def _ytd(self, employee_id: UUID, financial_year: str, ytd: dict) -> None:
        row = await self.payroll._opening_ytd(employee_id, financial_year)
        if row is None:
            row = PayrollOpeningYtd(
                id=uuid4(),
                school_id=self.payroll.school_id,
                employee_id=employee_id,
                financial_year=financial_year,
                source="IMPORT",
            )
            self.payroll.db.add(row)
        row.ytd_gross = _amount(ytd.get("ytd_gross"))
        row.ytd_tds = _amount(ytd.get("ytd_tds"))
        row.ytd_pf = _amount(ytd.get("ytd_pf"))
        row.ytd_esi = _amount(ytd.get("ytd_esi"))
        row.ytd_pt = _amount(ytd.get("ytd_pt"))
        row.source = "IMPORT"

    async def _template(self, import_id: UUID, components: dict[str, SalaryComponent], needed: list[str]) -> SalaryTemplate:
        template = SalaryTemplate(
            id=uuid4(),
            school_id=self.payroll.school_id,
            name=f"Import {import_id}"[:120],
            is_active=True,
        )
        self.payroll.db.add(template)
        await self.payroll.db.flush()
        for code in needed:
            self.payroll.db.add(
                SalaryTemplateLine(
                    id=uuid4(),
                    school_id=self.payroll.school_id,
                    template_id=template.id,
                    component_id=components[code].id,
                )
            )
        await self.payroll.db.flush()
        return template


def _financial_year(value: str | None) -> str:
    if not value:
        return financial_year_label(date.today())
    try:
        return canonical_financial_year(value)
    except ValueError as exc:
        raise AppError(str(exc), status_code=422) from exc


def _amount(value) -> Decimal:
    return Decimal(str(value or "0"))


def _public(row: PayrollAssistantImport) -> dict:
    preview = row.preview or {}
    rejected = preview.get("rejected") or []
    return mask_payload(
        {
            "id": str(row.id),
            "status": row.status,
            "filename": row.filename,
            "financial_year": preview.get("financial_year"),
            "effective_from": preview.get("effective_from"),
            "mapping": preview.get("mapping") or {},
            "accepted": preview.get("accepted") or [],
            "rejected": rejected,
            "counts": {
                "accepted": len(preview.get("accepted") or []),
                "rejected": len({item["row"] for item in rejected}),
            },
        }
    )
