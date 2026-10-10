"""School-configurable payroll. Existing /hr/payroll routes stay as they are."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.core.jobs import enqueue_job
from src.core.storage import put_object
from src.core.telemetry import track_alert, track_event
from src.db.models import (
    ComplianceEvent,
    Employee,
    EmployeeBankAccount,
    EmployeeSalaryAssignment,
    EmployeeStatutoryProfile,
    PayrollArrear,
    PayrollAuditLog,
    PayrollLoan,
    PayrollPayment,
    PayrollPayslip,
    PayrollPayslipLine,
    PayrollReimbursement,
    PayrollRoleGrant,
    PayrollRun,
    PayrollSchoolSettings,
    PayrollSettingRevision,
    PayrollSettlement,
    Notification,
    PtSlabRule,
    SalaryComponent,
    SalaryTemplate,
    SalaryTemplateLine,
    School,
    SchoolHoliday,
    StaffAttendance,
    StaffLeaveRequest,
    TaxDeclaration,
    TaxDeclarationProof,
    Teacher,
    User,
    WeeklyOffRule,
)
from src.modules.employees.service import sync_teacher_employees
from src.modules.payroll.bank import render_bank_file
from src.modules.payroll.calc import (
    SEEDED_PT_SLABS,
    ComponentDef,
    DayMark,
    FormulaError,
    Holiday,
    PayContext,
    PfInput,
    PtSlab,
    WeeklyOff,
    approval_transition,
    completed_years,
    compute_pay,
    eval_formula,
    financial_year_label,
    gratuity,
    last_working_day,
    loan_split,
    statutory_bonus,
    working_days,
)
from src.modules.payroll.notify import notify_payslip_published
from src.modules.payroll.pdf import build_form16_pdf, build_payslip_pdf
from src.modules.payroll.reports import professional_tax_csv, tds_24q_csv
from src.modules.payroll.permissions import (
    APPROVE,
    ATTENDANCE_ADMIN,
    AUDIT_READ,
    GRANT_WRITE,
    PREPARE,
    READ,
    SETTINGS_WRITE,
    require_payroll,
)
from src.modules.payroll.text import check_account, check_ifsc, check_pan, clean, require_text
from src.shared.enums import NotificationType, UserRole

logger = logging.getLogger("sybeez.payroll")
VISIBLE = ("PUBLISHED", "PAID")
TEMPLATES = {"CLASSIC", "COMPACT", "STATUTORY"}
_ZERO = Decimal("0.00")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _d(value) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class PayrollService:
    def __init__(self, db: AsyncSession, user: CurrentUser, *, bypass: bool = False):
        if user.school_id is None:
            raise ForbiddenError("School context required")
        self.db = db
        self.user = user
        self.school_id = user.school_id
        self.actor_id = user.id
        self.bypass = bypass

    async def _bind(self) -> None:
        tenant = {
            "school_id": str(self.school_id),
            "user_id": str(self.actor_id),
            "role": self.user.role.value,
            "bypass": "on" if self.bypass else "off",
        }
        self.db.info["rls_tenant"] = tenant
        for key, value in tenant.items():
            statement, params = text_set("rls_bypass" if key == "bypass" else key, value)
            await self.db.execute(statement, params)

    async def _allow(self, roles: set[str]) -> set[str]:
        await self._bind()
        return await require_payroll(self.db, self.user, roles)

    async def _audit(self, action: str, entity_type: str, entity_id: UUID | None, before, after, reason: str | None):
        self.db.add(
            PayrollAuditLog(
                id=uuid4(),
                school_id=self.school_id,
                actor_id=self.actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                reason=clean(reason, max_len=2000, field="reason") if reason else None,
                before=_jsonable(before) if before is not None else None,
                after=_jsonable(after) if after is not None else None,
                created_at=_now(),
            )
        )

    async def _employee_for_user(self, user_id: UUID | None = None) -> Employee:
        await sync_teacher_employees(self.db, self.school_id)
        uid = user_id or self.actor_id
        row = (
            await self.db.execute(
                select(Employee).where(Employee.school_id == self.school_id, Employee.user_id == uid)
            )
        ).scalar_one_or_none()
        if row is None:
            row = (
                await self.db.execute(
                    select(Employee)
                    .join(Teacher, Teacher.id == Employee.teacher_id)
                    .where(Employee.school_id == self.school_id, Teacher.user_id == uid)
                )
            ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("No employee record is linked to this login")
        return row

    async def _employee(self, employee_id: UUID) -> Employee:
        row = await self.db.get(Employee, employee_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Employee not found")
        return row

    async def load_settings(self) -> PayrollSchoolSettings | None:
        await self._bind()
        return (
            await self.db.execute(
                select(PayrollSchoolSettings).where(PayrollSchoolSettings.school_id == self.school_id)
            )
        ).scalar_one_or_none()

    async def ensure_settings(self) -> PayrollSchoolSettings:
        row = await self.load_settings()
        if row:
            return row
        row = PayrollSchoolSettings(id=uuid4(), school_id=self.school_id, effective_from=date.today(), updated_by=self.actor_id)
        self.db.add(row)
        self.db.add(
            WeeklyOffRule(id=uuid4(), school_id=self.school_id, weekday=6, occurrence="ALL", branch_name="")
        )
        await self.db.flush()
        await self._seed_components(row.effective_from)
        await self._seed_compliance()
        await self._audit("settings.create", "payroll_settings", row.id, None, {"pt_state": row.pt_state}, "Initial payroll settings")
        return row

    async def _seed_components(self, start: date) -> None:
        specs = [
            ("BASIC", "Basic", "EARNING", "FIXED", Decimal("0"), None, None, True, True, 1),
            ("HRA", "House rent allowance", "EARNING", "PERCENT", None, Decimal("40"), "BASIC", True, False, 2),
            ("DA", "Dearness allowance", "EARNING", "PERCENT", None, Decimal("0"), "BASIC", True, True, 3),
        ]
        ids = {}
        for code, name, kind, calc, amount, percent, percent_of, taxable, pf_wage, order in specs:
            comp = SalaryComponent(
                id=uuid4(),
                school_id=self.school_id,
                code=code,
                name=name,
                kind=kind,
                calc_type=calc,
                amount=amount or _ZERO,
                percent=percent,
                percent_of=percent_of,
                taxable=taxable,
                pf_wage=pf_wage,
                esi_wage=True,
                prorate=True,
                display_order=order,
                effective_from=start,
            )
            self.db.add(comp)
            ids[code] = comp.id
        template = SalaryTemplate(id=uuid4(), school_id=self.school_id, name="Standard", grade_code="STD", is_active=True)
        self.db.add(template)
        await self.db.flush()
        self.db.add(SalaryTemplateLine(id=uuid4(), school_id=self.school_id, template_id=template.id, component_id=ids["BASIC"], amount=Decimal("30000")))
        self.db.add(SalaryTemplateLine(id=uuid4(), school_id=self.school_id, template_id=template.id, component_id=ids["HRA"], percent=Decimal("40")))
        self.db.add(SalaryTemplateLine(id=uuid4(), school_id=self.school_id, template_id=template.id, component_id=ids["DA"], percent=Decimal("0")))

    async def _seed_compliance(self) -> None:
        today = date.today()
        for offset in range(0, 3):
            month = today.month + offset
            year = today.year + (month - 1) // 12
            month = (month - 1) % 12 + 1
            for title, day, kind in (
                ("PF ECR due", 15, "PF"),
                ("ESI contribution due", 15, "ESI"),
                ("TDS deposit due", 7, "TDS"),
            ):
                due = date(year, month, day)
                if due < today:
                    continue
                self.db.add(ComplianceEvent(id=uuid4(), school_id=self.school_id, title=title, due_on=due, kind=kind))

    async def get_settings(self) -> PayrollSchoolSettings:
        await self._allow(READ)
        return await self.ensure_settings()

    async def update_settings(self, data: dict) -> PayrollSchoolSettings:
        await self._allow(SETTINGS_WRITE)
        row = await self.ensure_settings()
        reason = require_text(data.pop("reason", "") or "", max_len=2000, field="reason")
        before = _settings_dict(row)
        _apply_settings(row, data)
        row.updated_by = self.actor_id
        row.effective_from = data.get("effective_from") or date.today()
        self.db.add(
            PayrollSettingRevision(
                id=uuid4(),
                school_id=self.school_id,
                effective_from=row.effective_from,
                snapshot=before,
                reason=reason,
                changed_by=self.actor_id,
            )
        )
        await self._audit("settings.update", "payroll_settings", row.id, before, _settings_dict(row), reason)
        await self.db.flush()
        return row

    async def list_setting_versions(self) -> list[PayrollSettingRevision]:
        await self._allow(READ)
        return list(
            (
                await self.db.execute(
                    select(PayrollSettingRevision)
                    .where(PayrollSettingRevision.school_id == self.school_id)
                    .order_by(PayrollSettingRevision.created_at.desc())
                )
            ).scalars()
        )

    async def list_components(self) -> list[SalaryComponent]:
        await self._allow(READ)
        return list(
            (
                await self.db.execute(
                    select(SalaryComponent)
                    .where(SalaryComponent.school_id == self.school_id, SalaryComponent.effective_to.is_(None))
                    .order_by(SalaryComponent.display_order, SalaryComponent.code)
                )
            ).scalars()
        )

    async def create_component(self, data: dict) -> SalaryComponent:
        await self._allow(SETTINGS_WRITE)
        await self.ensure_settings()
        code = require_text(data["code"], max_len=32, field="code").upper().replace(" ", "_")
        if code in {"PF", "VPF", "ESI", "PT", "TDS", "LOP", "GRATUITY", "EPS", "ARREARS", "BONUS", "REIMBURSEMENT", "LOAN"}:
            raise AppError(f"{code} is reserved", status_code=422)
        kind = data["kind"].upper()
        calc = data["calc_type"].upper()
        if kind not in {"EARNING", "DEDUCTION"} or calc not in {"FIXED", "PERCENT", "FORMULA"}:
            raise AppError("kind or calc_type is not valid", status_code=422)
        formula = clean(data.get("formula"), max_len=200, field="formula") if data.get("formula") else None
        if calc == "FORMULA":
            if not formula:
                raise AppError("formula is required", status_code=422)
            try:
                eval_formula(formula, {"BASIC": Decimal("1"), "DA": Decimal("1"), "HRA": Decimal("1"), "GROSS": Decimal("1")})
            except FormulaError as exc:
                raise AppError(str(exc), status_code=422) from exc
        if calc == "PERCENT" and not data.get("percent_of"):
            raise AppError("percent_of is required", status_code=422)
        row = SalaryComponent(
            id=uuid4(),
            school_id=self.school_id,
            code=code,
            name=require_text(data["name"], max_len=120, field="name"),
            kind=kind,
            calc_type=calc,
            amount=_d(data.get("amount")),
            percent=_d(data["percent"]) if data.get("percent") is not None else None,
            percent_of=(data.get("percent_of") or "").upper() or None,
            formula=formula,
            taxable=bool(data.get("taxable", True)),
            pf_wage=bool(data.get("pf_wage", False)),
            esi_wage=bool(data.get("esi_wage", True)),
            prorate=bool(data.get("prorate", True)),
            display_order=int(data.get("display_order") or 0),
            effective_from=data.get("effective_from") or date.today(),
        )
        self.db.add(row)
        await self._audit("component.create", "salary_component", row.id, None, {"code": code}, data.get("reason"))
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("A component with this code is already open") from exc
        return row

    async def retire_component(self, component_id: UUID, reason: str, effective_to: date | None) -> SalaryComponent:
        await self._allow(SETTINGS_WRITE)
        row = await self.db.get(SalaryComponent, component_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Component not found")
        before = {"effective_to": row.effective_to}
        row.effective_to = effective_to or date.today()
        await self._audit("component.retire", "salary_component", row.id, before, {"effective_to": row.effective_to}, reason)
        return row

    async def list_templates(self) -> list[dict]:
        await self._allow(READ)
        templates = list(
            (
                await self.db.execute(
                    select(SalaryTemplate).where(SalaryTemplate.school_id == self.school_id).order_by(SalaryTemplate.name)
                )
            ).scalars()
        )
        out = []
        for template in templates:
            lines = list(
                (
                    await self.db.execute(
                        select(SalaryTemplateLine).where(SalaryTemplateLine.template_id == template.id)
                    )
                ).scalars()
            )
            out.append({"template": template, "lines": lines})
        return out

    async def create_template(self, data: dict) -> dict:
        await self._allow(SETTINGS_WRITE)
        await self.ensure_settings()
        template = SalaryTemplate(
            id=uuid4(),
            school_id=self.school_id,
            name=require_text(data["name"], max_len=120, field="name"),
            grade_code=clean(data.get("grade_code"), max_len=32, field="grade code") if data.get("grade_code") else None,
            is_active=True,
        )
        self.db.add(template)
        await self.db.flush()
        lines = []
        for raw in data.get("lines") or []:
            component = await self.db.get(SalaryComponent, raw["component_id"])
            if component is None or component.school_id != self.school_id:
                raise NotFoundError("Component not found")
            line = SalaryTemplateLine(
                id=uuid4(),
                school_id=self.school_id,
                template_id=template.id,
                component_id=component.id,
                amount=_d(raw["amount"]) if raw.get("amount") is not None else None,
                percent=_d(raw["percent"]) if raw.get("percent") is not None else None,
            )
            self.db.add(line)
            lines.append(line)
        await self._audit("template.create", "salary_template", template.id, None, {"name": template.name}, data.get("reason"))
        return {"template": template, "lines": lines}

    async def assign_salary(self, data: dict) -> EmployeeSalaryAssignment:
        await self._allow(SETTINGS_WRITE)
        employee = await self._employee(data["employee_id"])
        template = await self.db.get(SalaryTemplate, data["template_id"])
        if template is None or template.school_id != self.school_id:
            raise NotFoundError("Salary template not found")
        start = data["effective_from"]
        reason = require_text(data.get("reason") or "Salary revision", max_len=2000, field="reason")
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
        if previous and previous.effective_from >= start:
            raise AppError("The new salary must start after the current one", status_code=422)
        if previous:
            previous.effective_to = start - timedelta(days=1)
        row = EmployeeSalaryAssignment(
            id=uuid4(),
            school_id=self.school_id,
            employee_id=employee.id,
            template_id=template.id,
            effective_from=start,
            overrides=data.get("overrides") or {},
            reason=reason,
        )
        self.db.add(row)
        if data.get("arrears_amount"):
            amount = _d(data["arrears_amount"])
            if amount > 0:
                self.db.add(
                    PayrollArrear(
                        id=uuid4(),
                        school_id=self.school_id,
                        employee_id=employee.id,
                        amount=amount,
                        reason=reason,
                        status="PENDING",
                    )
                )
        await self._audit(
            "salary.assign",
            "employee_salary",
            row.id,
            {"previous": str(previous.id) if previous else None},
            {"template_id": str(template.id), "effective_from": start.isoformat()},
            reason,
        )
        await self.db.flush()
        return row

    async def list_assignments(self, employee_id: UUID | None) -> list[dict]:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        stmt = (
            select(EmployeeSalaryAssignment, SalaryTemplate.name)
            .join(SalaryTemplate, SalaryTemplate.id == EmployeeSalaryAssignment.template_id)
            .where(EmployeeSalaryAssignment.school_id == self.school_id)
        )
        if held.isdisjoint(READ):
            employee = await self._employee_for_user()
            stmt = stmt.where(EmployeeSalaryAssignment.employee_id == employee.id)
        elif employee_id is not None:
            stmt = stmt.where(EmployeeSalaryAssignment.employee_id == employee_id)
        rows = (await self.db.execute(stmt.order_by(EmployeeSalaryAssignment.effective_from.desc()))).all()
        listed = []
        for assignment, template_name in rows:
            item = dump(assignment) or {}
            item["template_name"] = template_name
            listed.append(item)
        return listed

    async def upsert_profile(self, employee_id: UUID, data: dict) -> EmployeeStatutoryProfile:
        await self._allow(PREPARE | SETTINGS_WRITE)
        employee = await self._employee(employee_id)
        row = (
            await self.db.execute(
                select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
            )
        ).scalar_one_or_none()
        before = None
        if row is None:
            row = EmployeeStatutoryProfile(id=uuid4(), school_id=self.school_id, employee_id=employee.id)
            self.db.add(row)
        else:
            before = {"pf_enrolled": row.pf_enrolled, "esi_enrolled": row.esi_enrolled, "tax_regime": row.tax_regime}
        if "pan" in data:
            row.pan = check_pan(data.get("pan"))
        for field in (
            "uan",
            "pf_number",
            "esi_number",
            "gender",
            "tax_regime",
            "pt_state",
            "pf_wage_mode",
        ):
            if field in data and data[field] is not None:
                setattr(row, field, require_text(str(data[field]), max_len=32, field=field).upper())
        if "gender" in data and row.gender not in {"MALE", "FEMALE", "OTHER"}:
            raise AppError("gender must be MALE, FEMALE, or OTHER", status_code=422)
        if row.tax_regime and row.tax_regime not in {"NEW", "OLD"}:
            raise AppError("tax regime must be NEW or OLD", status_code=422)
        if row.pf_wage_mode and row.pf_wage_mode not in {"CAPPED", "FULL_BASIC"}:
            raise AppError("PF wage mode must be CAPPED or FULL_BASIC", status_code=422)
        for field in ("metro", "pf_enrolled", "esi_enrolled"):
            if field in data and data[field] is not None:
                setattr(row, field, bool(data[field]))
        for field in ("pf_employee_rate", "pf_employer_rate", "vpf_rate", "vpf_amount"):
            if field in data:
                setattr(row, field, _d(data[field]) if data[field] is not None else None)
        for field in ("pf_effective_from", "pf_effective_to", "esi_effective_from", "esi_effective_to"):
            if field in data:
                setattr(row, field, data[field])
        if "branch_name" in data:
            raw_branch = data.get("branch_name")
            row.branch_name = clean(raw_branch, max_len=120, field="branch") if raw_branch else None
        await self._audit("profile.update", "statutory_profile", row.id, before, {"pf_enrolled": row.pf_enrolled}, data.get("reason"))
        await self.db.flush()
        return row

    async def get_profile(self, employee_id: UUID) -> EmployeeStatutoryProfile | None:
        await self._allow(READ)
        employee = await self._employee(employee_id)
        return (
            await self.db.execute(
                select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
            )
        ).scalar_one_or_none()

    async def upsert_bank(self, employee_id: UUID, data: dict) -> EmployeeBankAccount:
        await self._allow(PREPARE | SETTINGS_WRITE)
        employee = await self._employee(employee_id)
        row = (
            await self.db.execute(
                select(EmployeeBankAccount).where(
                    EmployeeBankAccount.employee_id == employee.id, EmployeeBankAccount.is_primary.is_(True)
                )
            )
        ).scalar_one_or_none()
        before = {"ifsc": row.ifsc} if row else None
        if row is None:
            row = EmployeeBankAccount(id=uuid4(), school_id=self.school_id, employee_id=employee.id, is_primary=True)
            self.db.add(row)
        row.account_holder = require_text(data["account_holder"], max_len=120, field="account holder")
        row.account_number = check_account(data["account_number"])
        row.ifsc = check_ifsc(data["ifsc"])
        row.bank_name = require_text(data["bank_name"], max_len=120, field="bank name")
        await self._audit("bank.update", "bank_account", row.id, before, {"ifsc": row.ifsc}, data.get("reason"))
        await self.db.flush()
        return row

    async def my_profile(self) -> dict:
        await self._allow(READ | {UserRole.TEACHER.value})
        employee = await self._employee_for_user()
        profile = (
            await self.db.execute(
                select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
            )
        ).scalar_one_or_none()
        bank = (
            await self.db.execute(
                select(EmployeeBankAccount).where(
                    EmployeeBankAccount.employee_id == employee.id, EmployeeBankAccount.is_primary.is_(True)
                )
            )
        ).scalar_one_or_none()
        return {"employee_id": employee.id, "profile": profile, "bank": _mask_bank(bank)}

    async def replace_weekly_offs(self, rules: list[dict], reason: str) -> list[WeeklyOffRule]:
        await self._allow(SETTINGS_WRITE)
        await self.ensure_settings()
        reason = require_text(reason, max_len=2000, field="reason")
        before = [
            {"weekday": r.weekday, "occurrence": r.occurrence, "branch_name": r.branch_name}
            for r in (
                await self.db.execute(select(WeeklyOffRule).where(WeeklyOffRule.school_id == self.school_id))
            ).scalars()
        ]
        await self.db.execute(delete(WeeklyOffRule).where(WeeklyOffRule.school_id == self.school_id))
        saved = []
        for raw in rules:
            weekday = int(raw["weekday"])
            occurrence = str(raw.get("occurrence") or "ALL").upper()
            if weekday < 0 or weekday > 6 or occurrence not in {"ALL", "1", "2", "3", "4", "5", "LAST"}:
                raise AppError("Weekly off is not valid", status_code=422)
            row = WeeklyOffRule(
                id=uuid4(),
                school_id=self.school_id,
                weekday=weekday,
                occurrence=occurrence,
                branch_name=(raw.get("branch_name") or "").strip()[:120],
            )
            self.db.add(row)
            saved.append(row)
        await self._audit("weekly_off.replace", "weekly_off", None, {"rules": before}, {"count": len(saved)}, reason)
        await self.db.flush()
        return saved

    async def list_weekly_offs(self) -> list[WeeklyOffRule]:
        await self._allow(READ | {UserRole.TEACHER.value})
        return list(
            (await self.db.execute(select(WeeklyOffRule).where(WeeklyOffRule.school_id == self.school_id))).scalars()
        )

    async def add_holiday(self, data: dict) -> SchoolHoliday:
        await self._allow(SETTINGS_WRITE)
        kind = data["kind"].upper()
        if kind not in {"NATIONAL", "STATE", "SCHOOL", "OPTIONAL", "RESTRICTED"}:
            raise AppError("Holiday kind is not valid", status_code=422)
        row = SchoolHoliday(
            id=uuid4(),
            school_id=self.school_id,
            holiday_date=data["holiday_date"],
            name=require_text(data["name"], max_len=160, field="name"),
            kind=kind,
            branch_name=clean(data.get("branch_name"), max_len=120, field="branch") if data.get("branch_name") else None,
        )
        self.db.add(row)
        await self._audit("holiday.create", "holiday", row.id, None, {"date": row.holiday_date.isoformat(), "kind": kind}, data.get("reason"))
        await self.db.flush()
        return row

    async def list_holidays(self, year: int | None) -> list[SchoolHoliday]:
        await self._allow(READ | {UserRole.TEACHER.value})
        stmt = select(SchoolHoliday).where(SchoolHoliday.school_id == self.school_id)
        if year:
            stmt = stmt.where(func.extract("year", SchoolHoliday.holiday_date) == year)
        return list((await self.db.execute(stmt.order_by(SchoolHoliday.holiday_date))).scalars())

    async def delete_holiday(self, holiday_id: UUID, reason: str) -> None:
        await self._allow(SETTINGS_WRITE)
        row = await self.db.get(SchoolHoliday, holiday_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Holiday not found")
        await self._audit("holiday.delete", "holiday", row.id, {"name": row.name, "date": row.holiday_date.isoformat()}, None, reason)
        await self.db.delete(row)

    async def list_pt_slabs(self, state_code: str | None) -> list[PtSlabRule]:
        await self._allow(READ)
        state = state_code.upper() if state_code else None
        own = select(PtSlabRule).where(PtSlabRule.school_id == self.school_id)
        if state:
            own = own.where(PtSlabRule.state_code == state)
        rows = list((await self.db.execute(own)).scalars())
        if rows:
            return rows
        system = select(PtSlabRule).where(PtSlabRule.school_id.is_(None))
        if state:
            system = system.where(PtSlabRule.state_code == state)
        return list((await self.db.execute(system)).scalars())

    async def replace_pt_slabs(self, state_code: str, slabs: list[dict], reason: str) -> list[PtSlabRule]:
        await self._allow(SETTINGS_WRITE)
        state = require_text(state_code, max_len=8, field="state").upper()
        reason = require_text(reason, max_len=2000, field="reason")
        await self.db.execute(
            delete(PtSlabRule).where(PtSlabRule.school_id == self.school_id, PtSlabRule.state_code == state)
        )
        saved = []
        for raw in slabs:
            row = PtSlabRule(
                id=uuid4(),
                school_id=self.school_id,
                state_code=state,
                gender=str(raw.get("gender") or "ANY").upper(),
                min_amount=_d(raw.get("min_amount")),
                max_amount=_d(raw["max_amount"]) if raw.get("max_amount") is not None else None,
                amount=_d(raw.get("amount")),
                february_amount=_d(raw["february_amount"]) if raw.get("february_amount") is not None else None,
                frequency=str(raw.get("frequency") or "MONTHLY").upper(),
                basis=str(raw.get("basis") or "MONTHLY_GROSS").upper(),
            )
            if row.gender not in {"ANY", "MALE", "FEMALE", "OTHER"}:
                raise AppError("PT gender is not valid", status_code=422)
            self.db.add(row)
            saved.append(row)
        await self._audit("pt.replace", "pt_slab", None, None, {"state": state, "count": len(saved)}, reason)
        await self.db.flush()
        return saved

    async def create_loan(self, data: dict) -> PayrollLoan:
        await self._allow(PREPARE)
        employee = await self._employee(data["employee_id"])
        principal = _d(data["principal"])
        emi = _d(data["emi"])
        if principal <= 0 or emi <= 0:
            raise AppError("Principal and EMI must be greater than zero", status_code=422)
        row = PayrollLoan(
            id=uuid4(),
            school_id=self.school_id,
            employee_id=employee.id,
            kind=str(data.get("kind") or "LOAN").upper(),
            principal=principal,
            balance=principal,
            emi=emi,
            annual_rate=_d(data.get("annual_rate") or 0),
            start_date=data.get("start_date") or date.today(),
            status="ACTIVE",
        )
        if row.kind not in {"LOAN", "ADVANCE"}:
            raise AppError("kind must be LOAN or ADVANCE", status_code=422)
        self.db.add(row)
        await self._audit("loan.create", "loan", row.id, None, {"principal": str(principal)}, data.get("reason"))
        await self.db.flush()
        return row

    async def pause_loan(self, loan_id: UUID, paused_until: date, reason: str) -> PayrollLoan:
        await self._allow(PREPARE)
        row = await self.db.get(PayrollLoan, loan_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Loan not found")
        before = {"paused_until": row.paused_until}
        row.paused_until = paused_until
        await self._audit("loan.pause", "loan", row.id, before, {"paused_until": paused_until}, reason)
        return row

    async def list_loans(self, employee_id: UUID | None) -> list[PayrollLoan]:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        stmt = select(PayrollLoan).where(PayrollLoan.school_id == self.school_id)
        if held.isdisjoint(READ):
            employee = await self._employee_for_user()
            stmt = stmt.where(PayrollLoan.employee_id == employee.id)
        elif employee_id:
            stmt = stmt.where(PayrollLoan.employee_id == employee_id)
        return list((await self.db.execute(stmt.order_by(PayrollLoan.created_at.desc()))).scalars())

    async def create_arrear(self, data: dict) -> PayrollArrear:
        await self._allow(PREPARE)
        employee = await self._employee(data["employee_id"])
        amount = _d(data["amount"])
        if amount == 0:
            raise AppError("Arrear amount cannot be zero", status_code=422)
        row = PayrollArrear(
            id=uuid4(),
            school_id=self.school_id,
            employee_id=employee.id,
            amount=amount,
            reason=require_text(data["reason"], max_len=2000, field="reason"),
            status="PENDING",
        )
        self.db.add(row)
        await self._audit("arrear.create", "arrear", row.id, None, {"amount": str(amount)}, row.reason)
        await self.db.flush()
        return row

    async def list_arrears(self, employee_id: UUID | None, status: str | None) -> list[PayrollArrear]:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        stmt = select(PayrollArrear).where(PayrollArrear.school_id == self.school_id)
        if held.isdisjoint(READ):
            employee = await self._employee_for_user()
            stmt = stmt.where(PayrollArrear.employee_id == employee.id)
        elif employee_id is not None:
            stmt = stmt.where(PayrollArrear.employee_id == employee_id)
        if status:
            stmt = stmt.where(PayrollArrear.status == status.upper())
        return list((await self.db.execute(stmt.order_by(PayrollArrear.created_at.desc()))).scalars())

    async def create_reimbursement(self, data: dict) -> PayrollReimbursement:
        held = await self._allow(READ | PREPARE | {UserRole.TEACHER.value})
        if held.isdisjoint(PREPARE) or data.get("for_self"):
            employee = await self._employee_for_user()
        else:
            if not data.get("employee_id"):
                raise AppError("employee_id is required when creating a reimbursement for someone else", status_code=422)
            employee = await self._employee(data["employee_id"])
        row = PayrollReimbursement(
            id=uuid4(),
            school_id=self.school_id,
            employee_id=employee.id,
            amount=_d(data["amount"]),
            note=require_text(data["note"], max_len=2000, field="note"),
            status="PENDING",
        )
        if row.amount <= 0:
            raise AppError("Amount must be greater than zero", status_code=422)
        self.db.add(row)
        await self._audit("reimbursement.create", "reimbursement", row.id, None, {"amount": str(row.amount)}, row.note)
        await self.db.flush()
        return row

    async def decide_reimbursement(self, item_id: UUID, approve: bool, reason: str) -> PayrollReimbursement:
        await self._allow(PREPARE)
        row = await self.db.get(PayrollReimbursement, item_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Reimbursement not found")
        if row.status != "PENDING":
            raise ConflictError("This reimbursement is already decided")
        row.status = "APPROVED" if approve else "REJECTED"
        row.decided_by = self.actor_id
        await self._audit("reimbursement.decide", "reimbursement", row.id, {"status": "PENDING"}, {"status": row.status}, reason)
        return row

    async def list_reimbursements(self, employee_id: UUID | None, status: str | None) -> list[PayrollReimbursement]:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        stmt = select(PayrollReimbursement).where(PayrollReimbursement.school_id == self.school_id)
        if held.isdisjoint(READ):
            employee = await self._employee_for_user()
            stmt = stmt.where(PayrollReimbursement.employee_id == employee.id)
        elif employee_id is not None:
            stmt = stmt.where(PayrollReimbursement.employee_id == employee_id)
        if status:
            stmt = stmt.where(PayrollReimbursement.status == status.upper())
        return list((await self.db.execute(stmt.order_by(PayrollReimbursement.created_at.desc()))).scalars())

    async def upsert_declaration(self, data: dict, *, employee_id: UUID | None) -> TaxDeclaration:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        if employee_id and not held.isdisjoint(PREPARE | SETTINGS_WRITE):
            employee = await self._employee(employee_id)
        else:
            employee = await self._employee_for_user()
        fy = require_text(data["financial_year"], max_len=9, field="financial year")
        row = (
            await self.db.execute(
                select(TaxDeclaration).where(
                    TaxDeclaration.employee_id == employee.id, TaxDeclaration.financial_year == fy
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = TaxDeclaration(id=uuid4(), school_id=self.school_id, employee_id=employee.id, financial_year=fy)
            self.db.add(row)
        regime = str(data.get("regime") or "NEW").upper()
        if regime not in {"NEW", "OLD"}:
            raise AppError("regime must be NEW or OLD", status_code=422)
        row.regime = regime
        row.section_80c = _d(data.get("section_80c"))
        row.section_80d = _d(data.get("section_80d"))
        row.other_chapter_vi = _d(data.get("other_chapter_vi"))
        row.annual_rent = _d(data.get("annual_rent"))
        row.status = "SUBMITTED"
        await self._audit("declaration.save", "tax_declaration", row.id, None, {"fy": fy, "regime": regime}, data.get("reason"))
        await self.db.flush()
        return row

    async def my_declaration(self, financial_year: str | None) -> TaxDeclaration:
        await self._bind()
        employee = await self._employee_for_user()
        stmt = select(TaxDeclaration).where(
            TaxDeclaration.school_id == self.school_id,
            TaxDeclaration.employee_id == employee.id,
        )
        if financial_year:
            stmt = stmt.where(TaxDeclaration.financial_year == financial_year.strip())
        else:
            stmt = stmt.order_by(TaxDeclaration.financial_year.desc())
        row = (await self.db.execute(stmt)).scalars().first()
        if row is None:
            raise NotFoundError("No tax declaration has been saved")
        return row

    async def list_declarations(
        self, financial_year: str | None, employee_id: UUID | None, status: str | None
    ) -> list[TaxDeclaration]:
        await self._allow(READ)
        stmt = select(TaxDeclaration).where(TaxDeclaration.school_id == self.school_id)
        if financial_year:
            stmt = stmt.where(TaxDeclaration.financial_year == financial_year.strip())
        if employee_id is not None:
            stmt = stmt.where(TaxDeclaration.employee_id == employee_id)
        if status:
            stmt = stmt.where(TaxDeclaration.status == status.upper())
        return list((await self.db.execute(stmt.order_by(TaxDeclaration.financial_year.desc()))).scalars())

    async def add_proof(self, declaration_id: UUID, data: dict, content: bytes | None) -> TaxDeclarationProof:
        held = await self._allow(READ | {UserRole.TEACHER.value})
        row = await self.db.get(TaxDeclaration, declaration_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Declaration not found")
        if held.isdisjoint(PREPARE | SETTINGS_WRITE):
            employee = await self._employee_for_user()
            if row.employee_id != employee.id:
                raise ForbiddenError("You can upload proof only for your own declaration")
        key = None
        if content:
            if len(content) > 1_000_000:
                raise AppError("Proof file must be 1 MB or smaller", status_code=422)
            key = f"schools/{self.school_id}/tax-proofs/{uuid4().hex}"
            put_object(key=key, data=content, content_type="application/octet-stream")
        proof = TaxDeclarationProof(
            id=uuid4(),
            school_id=self.school_id,
            declaration_id=row.id,
            section=require_text(data["section"], max_len=32, field="section"),
            amount=_d(data["amount"]),
            object_key=key,
            note=clean(data.get("note"), max_len=500, field="note") if data.get("note") else None,
        )
        self.db.add(proof)
        await self._audit("declaration.proof", "tax_proof", proof.id, None, {"section": proof.section}, proof.note)
        await self.db.flush()
        return proof

    async def verify_declaration(self, declaration_id: UUID, reason: str) -> TaxDeclaration:
        await self._allow(PREPARE)
        row = await self.db.get(TaxDeclaration, declaration_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Declaration not found")
        before = {"status": row.status}
        row.status = "VERIFIED"
        await self._audit("declaration.verify", "tax_declaration", row.id, before, {"status": "VERIFIED"}, reason)
        return row

    async def grant_role(self, data: dict) -> PayrollRoleGrant:
        await self._allow(GRANT_WRITE)
        role = data["role"].upper()
        if role not in {"PAYROLL_ADMIN", "HR_MANAGER", "ACCOUNTANT", "PAYROLL_APPROVER", "AUDITOR"}:
            raise AppError("Unknown payroll role", status_code=422)
        row = PayrollRoleGrant(
            id=uuid4(),
            school_id=self.school_id,
            user_id=data["user_id"],
            role=role,
            effective_from=data.get("effective_from") or date.today(),
            effective_to=data.get("effective_to"),
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("This person already has that payroll role") from exc
        await self._audit("role.grant", "payroll_role", row.id, None, {"user_id": str(row.user_id), "role": role}, data.get("reason"))
        return row

    async def revoke_role(self, grant_id: UUID, reason: str) -> None:
        await self._allow(GRANT_WRITE)
        row = await self.db.get(PayrollRoleGrant, grant_id)
        if row is None or row.school_id != self.school_id:
            raise NotFoundError("Grant not found")
        await self._audit("role.revoke", "payroll_role", row.id, {"role": row.role, "user_id": str(row.user_id)}, None, reason)
        await self.db.delete(row)

    async def list_grants(self) -> list[PayrollRoleGrant]:
        await self._allow(READ)
        return list(
            (
                await self.db.execute(
                    select(PayrollRoleGrant).where(PayrollRoleGrant.school_id == self.school_id)
                )
            ).scalars()
        )

    async def list_audit(self, limit: int = 200) -> list[PayrollAuditLog]:
        await self._allow(AUDIT_READ)
        return list(
            (
                await self.db.execute(
                    select(PayrollAuditLog)
                    .where(PayrollAuditLog.school_id == self.school_id)
                    .order_by(PayrollAuditLog.created_at.desc())
                    .limit(min(limit, 500))
                )
            ).scalars()
        )

    async def list_compliance(self) -> list[ComplianceEvent]:
        await self._allow(READ)
        return list(
            (
                await self.db.execute(
                    select(ComplianceEvent)
                    .where(ComplianceEvent.school_id == self.school_id)
                    .order_by(ComplianceEvent.due_on)
                )
            ).scalars()
        )

    async def add_compliance(self, data: dict) -> ComplianceEvent:
        await self._allow(SETTINGS_WRITE)
        row = ComplianceEvent(
            id=uuid4(),
            school_id=self.school_id,
            title=require_text(data["title"], max_len=160, field="title"),
            due_on=data["due_on"],
            kind=require_text(data.get("kind") or "SCHOOL", max_len=32, field="kind").upper(),
            notes=clean(data.get("notes"), max_len=2000, field="notes") if data.get("notes") else None,
        )
        self.db.add(row)
        await self._audit("compliance.create", "compliance", row.id, None, {"title": row.title, "due_on": row.due_on.isoformat()}, data.get("reason"))
        await self.db.flush()
        return row

    async def preview(self, employee_id: UUID, period_start: date, period_end: date) -> dict:
        await self._allow(READ)
        if period_end < period_start:
            raise AppError("period_end must be on or after period_start", status_code=422)
        pay, _meta = await self._compute_employee(employee_id, period_start, period_end, persist_loans=False)
        return _pay_dict(pay)

    async def generate(
        self,
        period_start: date,
        period_end: date,
        *,
        source: str = "MANUAL",
        reason: str | None = None,
    ) -> dict:
        await self._allow(PREPARE)
        if period_end < period_start:
            raise AppError("period_end must be on or after period_start", status_code=422)
        await sync_teacher_employees(self.db, self.school_id)
        existing = (
            await self.db.execute(
                select(PayrollRun).where(
                    PayrollRun.school_id == self.school_id,
                    PayrollRun.period_start == period_start,
                    PayrollRun.period_end == period_end,
                    PayrollRun.run_kind == "STATUTORY",
                    PayrollRun.approval_status.notin_(("REJECTED", "CANCELLED")),
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {"run": existing, "created": False, "skipped": []}
        settings = await self.ensure_settings()
        weekly, holidays = await self._calendar()
        pay_date = _pay_date(settings, period_end, weekly, holidays)
        run = PayrollRun(
            id=uuid4(),
            school_id=self.school_id,
            label=f"Payroll {period_start:%b %Y}",
            period_start=period_start,
            period_end=period_end,
            status="DRAFT",
            run_kind="STATUTORY",
            approval_status="DRAFT",
            source=source,
            created_by=self.actor_id,
            pay_date=pay_date,
        )
        self.db.add(run)
        await self.db.flush()
        employees = list(
            (
                await self.db.execute(
                    select(Employee).where(Employee.school_id == self.school_id, Employee.status == "ACTIVE")
                )
            ).scalars()
        )
        skipped = []
        try:
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
            if source == "ATTENDANCE_LOCK":
                run.approval_status = "PENDING_APPROVAL"
                run.submitted_by = self.actor_id
                run.submitted_at = _now()
                if settings.auto_publish:
                    run.approval_status = "APPROVED"
                    run.approved_at = _now()
            await self.db.flush()
            if source == "ATTENDANCE_LOCK" and settings.auto_publish:
                await self._publish_run(run, settings, reason or "auto_publish setting")
        except Exception as exc:
            track_alert("payroll_run_failed", school_id=str(self.school_id), error=type(exc).__name__)
            logger.exception("payroll run failed")
            raise
        await self._audit(
            "run.generate",
            "payroll_run",
            run.id,
            None,
            {"period_start": period_start.isoformat(), "skipped": skipped, "source": source},
            reason or source,
        )
        track_event("payroll_run_generated", school_id=str(self.school_id), run_id=str(run.id), source=source)
        return {"run": run, "created": True, "skipped": skipped}

    async def _assignment(self, employee_id: UUID, on: date) -> EmployeeSalaryAssignment | None:
        return (
            await self.db.execute(
                select(EmployeeSalaryAssignment)
                .where(
                    EmployeeSalaryAssignment.school_id == self.school_id,
                    EmployeeSalaryAssignment.employee_id == employee_id,
                    EmployeeSalaryAssignment.effective_from <= on,
                    or_(EmployeeSalaryAssignment.effective_to.is_(None), EmployeeSalaryAssignment.effective_to >= on),
                )
                .order_by(EmployeeSalaryAssignment.effective_from.desc())
            )
        ).scalars().first()

    async def _calendar(self) -> tuple[list[WeeklyOff], list[Holiday]]:
        rules = list(
            (await self.db.execute(select(WeeklyOffRule).where(WeeklyOffRule.school_id == self.school_id))).scalars()
        )
        holidays = list(
            (await self.db.execute(select(SchoolHoliday).where(SchoolHoliday.school_id == self.school_id))).scalars()
        )
        return (
            [WeeklyOff(r.weekday, r.occurrence) for r in rules if not r.branch_name],
            [Holiday(h.holiday_date, h.kind, h.branch_name) for h in holidays],
        )

    async def _compute_employee(self, employee_id: UUID, start: date, end: date, *, persist_loans: bool):
        settings = await self.ensure_settings()
        employee = await self._employee(employee_id)
        assignment = await self._assignment(employee.id, end)
        if assignment is None:
            raise AppError("This employee has no salary assignment for the period", status_code=422)
        components = await self._resolved_components(assignment)
        weekly, holidays = await self._calendar()
        marks = await self._marks(employee, start, end, weekly, holidays)
        profile = (
            await self.db.execute(
                select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
            )
        ).scalar_one_or_none()
        declaration = (
            await self.db.execute(
                select(TaxDeclaration).where(
                    TaxDeclaration.employee_id == employee.id,
                    TaxDeclaration.financial_year == financial_year_label(end),
                    TaxDeclaration.status.in_(("SUBMITTED", "VERIFIED")),
                )
            )
        ).scalar_one_or_none()
        arrears = list(
            (
                await self.db.execute(
                    select(PayrollArrear).where(
                        PayrollArrear.employee_id == employee.id, PayrollArrear.status == "PENDING"
                    )
                )
            ).scalars()
        )
        reims = list(
            (
                await self.db.execute(
                    select(PayrollReimbursement).where(
                        PayrollReimbursement.employee_id == employee.id,
                        PayrollReimbursement.status == "APPROVED",
                        PayrollReimbursement.payslip_id.is_(None),
                    )
                )
            ).scalars()
        )
        loans = list(
            (
                await self.db.execute(
                    select(PayrollLoan).where(
                        PayrollLoan.employee_id == employee.id,
                        PayrollLoan.status == "ACTIVE",
                        PayrollLoan.start_date <= end,
                        or_(PayrollLoan.paused_until.is_(None), PayrollLoan.paused_until < start),
                    )
                )
            ).scalars()
        )
        basic_guess = next((c.amount for c in components if c.code == "BASIC"), _ZERO)
        da_guess = next((c.amount for c in components if c.code == "DA"), _ZERO)
        bonus = statutory_bonus(
            enabled=settings.bonus_enabled,
            month=end.month,
            bonus_month=settings.bonus_month,
            basic=basic_guess,
            da=da_guess,
            percent=settings.bonus_percent,
            wage_ceiling=settings.bonus_wage_ceiling,
            calc_ceiling=settings.bonus_calc_ceiling,
            rounding=settings.rounding_mode,
        )
        name = employee.full_name or "Employee"
        if employee.teacher_id:
            teacher = await self.db.get(Teacher, employee.teacher_id)
            if teacher and teacher.user_id:
                user = await self.db.get(User, teacher.user_id)
                if user:
                    name = user.full_name
        elif employee.user_id:
            user = await self.db.get(User, employee.user_id)
            if user:
                name = user.full_name
        pf = _pf_input(settings, profile, end)
        slabs = await self._pt_objects(profile.pt_state if profile and profile.pt_state else settings.pt_state)
        common = dict(
            components=components,
            period_start=start,
            period_end=end,
            weekly=weekly,
            holidays=holidays,
            marks=marks,
            pf=pf,
            esi_threshold=settings.esi_wage_ceiling,
            esi_employee_rate=settings.esi_employee_rate,
            esi_employer_rate=settings.esi_employer_rate,
            esi_enrolled=bool(profile and profile.esi_enrolled),
            esi_from=profile.esi_effective_from if profile else None,
            esi_to=profile.esi_effective_to if profile else None,
            pt_slabs=slabs,
            pt_state=(profile.pt_state if profile and profile.pt_state else settings.pt_state),
            gender=(profile.gender if profile else "OTHER"),
            regime=(profile.tax_regime if profile and profile.tax_regime else (declaration.regime if declaration else settings.tax_regime_default)),
            metro=bool(profile and profile.metro),
            annual_rent=declaration.annual_rent if declaration else _ZERO,
            declared_80c=declaration.section_80c if declaration else _ZERO,
            declared_80d=declaration.section_80d if declaration else _ZERO,
            other_chapter_vi=declaration.other_chapter_vi if declaration else _ZERO,
            lop_base=settings.lop_base,
            rounding=settings.rounding_mode,
            arrears=sum((a.amount for a in arrears), _ZERO),
            reimbursements=sum((r.amount for r in reims), _ZERO),
            bonus=bonus,
            employee_name=name,
            pan=profile.pan if profile else None,
        )
        draft = compute_pay(PayContext(**common, loan_emi=_ZERO))
        room = draft.gross - draft.deductions
        emi = min(sum((loan.emi for loan in loans), _ZERO), room if room > 0 else _ZERO)
        pay = compute_pay(PayContext(**common, loan_emi=emi))
        return pay, loans if persist_loans else []

    async def _resolved_components(self, assignment: EmployeeSalaryAssignment) -> list[ComponentDef]:
        lines = list(
            (
                await self.db.execute(
                    select(SalaryTemplateLine).where(SalaryTemplateLine.template_id == assignment.template_id)
                )
            ).scalars()
        )
        overrides = assignment.overrides or {}
        resolved = []
        for line in lines:
            component = await self.db.get(SalaryComponent, line.component_id)
            if component is None or component.effective_to is not None:
                continue
            amount = line.amount if line.amount is not None else component.amount
            percent = line.percent if line.percent is not None else component.percent
            raw = overrides.get(component.code) or overrides.get(component.code.upper())
            if isinstance(raw, dict):
                if raw.get("amount") is not None:
                    amount = _d(raw["amount"])
                if raw.get("percent") is not None:
                    percent = _d(raw["percent"])
            elif raw is not None:
                if component.calc_type == "PERCENT":
                    percent = _d(raw)
                else:
                    amount = _d(raw)
            resolved.append(
                ComponentDef(
                    code=component.code,
                    name=component.name,
                    kind=component.kind,
                    calc_type=component.calc_type,
                    amount=amount or _ZERO,
                    percent=percent,
                    percent_of=component.percent_of,
                    formula=component.formula,
                    taxable=component.taxable,
                    pf_wage=component.pf_wage,
                    esi_wage=component.esi_wage,
                    prorate=component.prorate,
                    display_order=component.display_order,
                )
            )
        return resolved

    async def _marks(self, employee: Employee, start: date, end: date, weekly, holidays) -> list[DayMark]:
        rows = list(
            (
                await self.db.execute(
                    select(StaffAttendance).where(
                        StaffAttendance.employee_id == employee.id,
                        StaffAttendance.attendance_date >= start,
                        StaffAttendance.attendance_date <= end,
                    )
                )
            ).scalars()
        )
        by_day = {row.attendance_date: row.status for row in rows}
        leaves = list(
            (
                await self.db.execute(
                    select(StaffLeaveRequest).where(
                        StaffLeaveRequest.school_id == self.school_id,
                        StaffLeaveRequest.status == "APPROVED",
                        StaffLeaveRequest.end_date >= start,
                        StaffLeaveRequest.start_date <= end,
                        or_(
                            StaffLeaveRequest.user_id == employee.user_id,
                            StaffLeaveRequest.user_id.in_(
                                select(Teacher.user_id).where(Teacher.id == employee.teacher_id)
                            )
                            if employee.teacher_id
                            else select(StaffLeaveRequest.user_id).where(StaffLeaveRequest.id.is_(None)),
                        ),
                    )
                )
            ).scalars()
        )
        paid = set()
        for leave in leaves:
            if not leave.is_paid:
                continue
            day = max(leave.start_date, start)
            while day <= min(leave.end_date, end):
                paid.add(day)
                day += timedelta(days=1)
        marks = []
        for day in working_days(start, end, weekly, holidays):
            status = by_day.get(day, "UNMARKED")
            if status == "UNMARKED" and day in paid:
                status = "LEAVE"
            marks.append(DayMark(day, status, paid_leave=day in paid))
        return marks

    async def _pt_objects(self, state: str) -> list[PtSlab]:
        rows = await self.list_pt_slabs(state)
        if not rows:
            return [s for s in SEEDED_PT_SLABS if s.state_code == state.upper()]
        return [
            PtSlab(
                r.state_code,
                r.gender,
                r.min_amount,
                r.max_amount,
                r.amount,
                r.february_amount,
                r.frequency,
                r.basis,
            )
            for r in rows
        ]

    async def _consume_inputs(self, employee_id: UUID, payslip_id: UUID, loans: list[PayrollLoan], pay) -> None:
        for arrear in (
            await self.db.execute(
                select(PayrollArrear).where(PayrollArrear.employee_id == employee_id, PayrollArrear.status == "PENDING")
            )
        ).scalars():
            arrear.status = "APPLIED"
            arrear.payslip_id = payslip_id
        for item in (
            await self.db.execute(
                select(PayrollReimbursement).where(
                    PayrollReimbursement.employee_id == employee_id,
                    PayrollReimbursement.status == "APPROVED",
                    PayrollReimbursement.payslip_id.is_(None),
                )
            )
        ).scalars():
            item.payslip_id = payslip_id
            item.status = "PAID"
        remaining = next((line.amount for line in pay.lines if line.code == "LOAN"), _ZERO)
        for loan in loans:
            if remaining <= 0:
                break
            take = min(loan.emi, remaining, loan.balance)
            _interest, principal, balance = loan_split(
                balance=loan.balance, emi=take, annual_rate=loan.annual_rate
            )
            loan.balance = balance
            if loan.balance <= 0:
                loan.status = "CLOSED"
                loan.balance = _ZERO
            remaining -= take

    async def list_runs(self) -> list[PayrollRun]:
        await self._allow(READ)
        return list(
            (
                await self.db.execute(
                    select(PayrollRun)
                    .where(PayrollRun.school_id == self.school_id)
                    .order_by(PayrollRun.period_start.desc())
                )
            ).scalars()
        )

    async def get_run(self, run_id: UUID) -> dict:
        await self._allow(READ)
        run = await self._run(run_id)
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip).where(PayrollPayslip.payroll_run_id == run.id)
                )
            ).scalars()
        )
        detailed = []
        for slip in slips:
            lines = list(
                (
                    await self.db.execute(
                        select(PayrollPayslipLine)
                        .where(PayrollPayslipLine.payslip_id == slip.id)
                        .order_by(PayrollPayslipLine.display_order)
                    )
                ).scalars()
            )
            detailed.append({"payslip": slip, "lines": lines})
        return {"run": run, "payslips": detailed}

    async def transition(self, run_id: UUID, action: str, reason: str | None) -> PayrollRun:
        roles = APPROVE if action in {"approve", "reject"} else PREPARE
        held = await self._allow(roles)
        run = await self._run(run_id)
        if run.run_kind != "STATUTORY" and action != "lock":
            raise AppError("This action applies to a statutory payroll run", status_code=422)
        if action in {"approve", "reject"} and UserRole.AUDITOR.value in held and held.isdisjoint(APPROVE - {UserRole.AUDITOR.value}):
            raise ForbiddenError("Auditors cannot decide payroll")
        settings = await self.ensure_settings()
        try:
            new_status = approval_transition(
                status=run.approval_status or "DRAFT",
                action=action.upper(),
                actor_id=str(self.actor_id),
                submitter_id=str(run.submitted_by) if run.submitted_by else None,
                actor_can_approve=not held.isdisjoint(APPROVE),
                require_different_user=settings.require_different_approver,
            )
        except ValueError as exc:
            raise AppError(str(exc), status_code=422) from exc
        before = {"approval_status": run.approval_status}
        run.approval_status = new_status
        if action == "submit":
            run.submitted_by = self.actor_id
            run.submitted_at = _now()
            run.rejection_reason = None
        elif action == "approve":
            run.approved_by = self.actor_id
            run.approved_at = _now()
            run.finalized_by = self.actor_id
            run.finalized_at = _now()
        elif action == "reject":
            if not reason:
                raise AppError("A reason is required to reject a payroll run", status_code=422)
            run.rejected_by = self.actor_id
            run.rejection_reason = require_text(reason, max_len=2000, field="reason")
            run.approval_status = "REJECTED"
        elif action == "lock":
            run.locked_at = _now()
            run.approval_status = "LOCKED"
        await self._audit(f"run.{action}", "payroll_run", run.id, before, {"approval_status": run.approval_status}, reason)
        if action == "approve":
            await self._publish_run(run, settings, reason or "Approved")
        return run

    async def _publish_run(self, run: PayrollRun, settings: PayrollSchoolSettings, reason: str) -> None:
        school = await self.db.get(School, self.school_id)
        slips = list(
            (await self.db.execute(select(PayrollPayslip).where(PayrollPayslip.payroll_run_id == run.id))).scalars()
        )
        for slip in slips:
            if slip.status == "CANCELLED":
                continue
            lines = list(
                (
                    await self.db.execute(select(PayrollPayslipLine).where(PayrollPayslipLine.payslip_id == slip.id))
                ).scalars()
            )
            employee = await self.db.get(Employee, slip.employee_id) if slip.employee_id else None
            profile = None
            bank = None
            if employee:
                profile = (
                    await self.db.execute(
                        select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
                    )
                ).scalar_one_or_none()
                bank = (
                    await self.db.execute(
                        select(EmployeeBankAccount).where(
                            EmployeeBankAccount.employee_id == employee.id, EmployeeBankAccount.is_primary.is_(True)
                        )
                    )
                ).scalar_one_or_none()
            payload = _pdf_payload(school, settings, run, slip, lines, employee, profile, bank)
            try:
                pdf = build_payslip_pdf(payload)
                key = f"schools/{self.school_id}/payslips/{slip.id}.pdf"
                slip.pdf_object_key = put_object(key=key, data=pdf, content_type="application/pdf")
            except Exception as exc:
                track_alert("payroll_payslip_pdf_failed", payslip_id=str(slip.id), error=type(exc).__name__)
                logger.exception("payslip pdf failed")
                raise AppError("Could not store the payslip PDF", status_code=500) from exc
            slip.status = "PUBLISHED"
            slip.published_at = _now()
            user_id = None
            if employee and employee.user_id:
                user_id = employee.user_id
            elif employee and employee.teacher_id:
                teacher = await self.db.get(Teacher, employee.teacher_id)
                user_id = teacher.user_id if teacher else None
            await notify_payslip_published(
                self.db,
                school_id=self.school_id,
                user_id=user_id,
                payslip_id=slip.id,
                period_label=run.label,
                net=str(slip.net_amount),
            )
        run.published_at = _now()
        run.approval_status = "PUBLISHED"
        await self._audit("run.publish", "payroll_run", run.id, None, {"payslips": len(slips)}, reason)

    async def my_payslips(self, year: int | None) -> list[PayrollPayslip]:
        await self._bind()
        employee = await self._employee_for_user()
        stmt = select(PayrollPayslip).where(
            PayrollPayslip.school_id == self.school_id,
            PayrollPayslip.employee_id == employee.id,
            PayrollPayslip.status.in_(VISIBLE),
        )
        if year:
            stmt = stmt.where(func.extract("year", PayrollPayslip.created_at) == year)
        return list((await self.db.execute(stmt.order_by(PayrollPayslip.created_at.desc()))).scalars())

    async def payslip_pdf(self, payslip_id: UUID, *, intent: str = "download") -> tuple[bytes, str]:
        await self._bind()
        slip = await self.db.get(PayrollPayslip, payslip_id)
        if slip is None or slip.school_id != self.school_id:
            raise NotFoundError("Payslip not found")
        held = set()
        try:
            held = await require_payroll(self.db, self.user, READ)
        except ForbiddenError:
            held = set()
        owns = False
        if held.isdisjoint(READ):
            employee = await self._employee_for_user()
            if slip.employee_id != employee.id or slip.status not in VISIBLE:
                raise NotFoundError("Payslip not found")
            owns = True
        else:
            try:
                me = await self._employee_for_user()
                owns = slip.employee_id == me.id
            except NotFoundError:
                owns = False
        if owns:
            _bump_access(slip, intent)
        if slip.pdf_object_key:
            from pathlib import Path

            from src.core.config import get_settings

            settings = get_settings()
            if not (settings.s3_bucket or "").strip() and (settings.app_env or "").lower() != "production":
                path = Path(__file__).resolve().parents[3] / "media" / slip.pdf_object_key
                if path.exists():
                    return path.read_bytes(), f"payslip-{slip.id}.pdf"
        run = await self.db.get(PayrollRun, slip.payroll_run_id)
        school = await self.db.get(School, self.school_id)
        settings_row = _settings_for_pdf(await self.load_settings(), self.school_id)
        lines = list(
            (await self.db.execute(select(PayrollPayslipLine).where(PayrollPayslipLine.payslip_id == slip.id))).scalars()
        )
        employee = await self.db.get(Employee, slip.employee_id) if slip.employee_id else None
        pdf = build_payslip_pdf(_pdf_payload(school, settings_row, run, slip, lines, employee, None, None))
        return pdf, f"payslip-{slip.id}.pdf"

    async def form16(self, employee_id: UUID | None, financial_year: str) -> dict:
        await self._bind()
        if employee_id is None:
            employee = await self._employee_for_user()
        else:
            await self._allow(READ)
            employee = await self._employee(employee_id)
        start_year = int(financial_year[:4])
        start, end = date(start_year, 4, 1), date(start_year + 1, 3, 31)
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip)
                    .join(PayrollRun, PayrollRun.id == PayrollPayslip.payroll_run_id)
                    .where(
                        PayrollPayslip.employee_id == employee.id,
                        PayrollPayslip.status.in_(VISIBLE),
                        PayrollRun.period_end >= start,
                        PayrollRun.period_start <= end,
                    )
                )
            ).scalars()
        )
        gross = sum((s.gross_amount for s in slips), _ZERO)
        tds_total = _ZERO
        for slip in slips:
            tds_total += sum(
                (
                    line.amount
                    for line in (
                        await self.db.execute(
                            select(PayrollPayslipLine).where(
                                PayrollPayslipLine.payslip_id == slip.id, PayrollPayslipLine.code == "TDS"
                            )
                        )
                    ).scalars()
                ),
                _ZERO,
            )
        latest = slips[-1].form16 if slips and slips[-1].form16 else {}
        return {
            "employee_id": str(employee.id),
            "financial_year": financial_year,
            "payslip_count": len(slips),
            "gross": str(gross),
            "tds_deducted": str(tds_total),
            "snapshot": latest,
        }

    async def form16_pdf(self, employee_id: UUID | None, financial_year: str) -> tuple[bytes, str]:
        data = await self.form16(employee_id, financial_year)
        return build_form16_pdf(data), f"form16-{financial_year}.pdf"

    async def list_payslip_notifications(self, limit: int = 50) -> list[Notification]:
        await self._bind()
        stmt = (
            select(Notification)
            .where(
                Notification.school_id == self.school_id,
                Notification.user_id == self.actor_id,
                Notification.category == NotificationType.PAYSLIP_PUBLISHED.value,
            )
            .order_by(Notification.created_at.desc())
            .limit(min(max(limit, 1), 200))
        )
        return list((await self.db.execute(stmt)).scalars())

    async def _employee_label(self, employee: Employee | None) -> tuple[str, str | None]:
        if employee is None:
            return "Employee", None
        name = employee.full_name or "Employee"
        if employee.teacher_id:
            teacher = await self.db.get(Teacher, employee.teacher_id)
            if teacher and teacher.user_id:
                user = await self.db.get(User, teacher.user_id)
                if user and user.full_name:
                    name = user.full_name
        elif employee.user_id:
            user = await self.db.get(User, employee.user_id)
            if user and user.full_name:
                name = user.full_name
        return name, employee.employee_code

    async def pt_report(self, run_id: UUID) -> tuple[str, str]:
        detail = await self.get_run(run_id)
        settings = await self.load_settings()
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
            name, code = await self._employee_label(employee)
            amounts = {line.code: line.amount for line in item["lines"]}
            rows.append(
                {
                    "employee_id": str(slip.employee_id) if slip.employee_id else None,
                    "employee_name": name,
                    "employee_code": code,
                    "state": (profile.pt_state if profile and profile.pt_state else (settings.pt_state if settings else None)),
                    "gender": profile.gender if profile else None,
                    "pt_amount": str(amounts.get("PT", _ZERO)),
                }
            )
        return professional_tax_csv(rows), "text/csv"

    async def tds_24q_report(self, run_id: UUID) -> tuple[str, str]:
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
            name, _code = await self._employee_label(employee)
            amounts = {line.code: line.amount for line in item["lines"]}
            rows.append(
                {
                    "employee_id": str(slip.employee_id) if slip.employee_id else None,
                    "employee_name": name,
                    "pan": profile.pan if profile else None,
                    "gross_amount": str(slip.gross_amount),
                    "tds_amount": str(amounts.get("TDS", _ZERO)),
                    "tax_regime": slip.tax_regime,
                }
            )
        return tds_24q_csv(rows), "text/csv"

    async def salary_register(self, run_id: UUID) -> list[dict]:
        detail = await self.get_run(run_id)
        rows = []
        for item in detail["payslips"]:
            slip = item["payslip"]
            rows.append(
                {
                    "employee_id": str(slip.employee_id) if slip.employee_id else None,
                    "gross": str(slip.gross_amount),
                    "deductions": str(slip.deductions_amount),
                    "net": str(slip.net_amount),
                    "lop_days": str(slip.lop_days or 0),
                    "lines": [{"code": line.code, "name": line.name, "kind": line.kind, "amount": str(line.amount)} for line in item["lines"]],
                }
            )
        return rows

    async def challan(self, run_id: UUID, kind: str) -> list[dict]:
        detail = await self.get_run(run_id)
        code = "PF" if kind == "pf" else "ESI"
        rows = []
        for item in detail["payslips"]:
            slip = item["payslip"]
            employee = await self.db.get(Employee, slip.employee_id) if slip.employee_id else None
            profile = None
            if employee:
                profile = (
                    await self.db.execute(
                        select(EmployeeStatutoryProfile).where(EmployeeStatutoryProfile.employee_id == employee.id)
                    )
                ).scalar_one_or_none()
            amounts = {line.code: line.amount for line in item["lines"]}
            if kind == "pf":
                rows.append(
                    {
                        "employee_id": str(slip.employee_id) if slip.employee_id else None,
                        "uan": profile.uan if profile else None,
                        "wage": str(amounts.get("BASIC", _ZERO)),
                        "employee_share": str(amounts.get("PF", _ZERO)),
                        "voluntary": str(amounts.get("VPF", _ZERO)),
                        "employer_share": str(amounts.get("PF_EMPLOYER", _ZERO)),
                        "eps": str(amounts.get("EPS", _ZERO)),
                    }
                )
            else:
                rows.append(
                    {
                        "employee_id": str(slip.employee_id) if slip.employee_id else None,
                        "esi_number": profile.esi_number if profile else None,
                        "employee_share": str(amounts.get("ESI", _ZERO)),
                        "employer_share": str(amounts.get("ESI_EMPLOYER", _ZERO)),
                    }
                )
        return rows

    async def bank_file(self, run_id: UUID) -> tuple[str, str]:
        await self._allow(PREPARE)
        run = await self._run(run_id)
        if run.approval_status not in {"APPROVED", "PUBLISHED", "PAID", "LOCKED"}:
            raise AppError("Export a bank file only after the run is approved", status_code=422)
        settings = await self.ensure_settings()
        slips = list(
            (
                await self.db.execute(
                    select(PayrollPayslip).where(
                        PayrollPayslip.payroll_run_id == run.id, PayrollPayslip.status != "CANCELLED"
                    )
                )
            ).scalars()
        )
        export_rows = []
        try:
            for slip in slips:
                employee = await self.db.get(Employee, slip.employee_id) if slip.employee_id else None
                bank = None
                if employee:
                    bank = (
                        await self.db.execute(
                            select(EmployeeBankAccount).where(
                                EmployeeBankAccount.employee_id == employee.id,
                                EmployeeBankAccount.is_primary.is_(True),
                            )
                        )
                    ).scalar_one_or_none()
                if bank is None:
                    continue
                export_rows.append(
                    {
                        "beneficiary_name": bank.account_holder,
                        "account_number": bank.account_number,
                        "ifsc": bank.ifsc,
                        "amount": slip.net_amount,
                        "narration": f"Salary {run.label}",
                        "employee_code": employee.employee_code if employee else "",
                    }
                )
                payment = (
                    await self.db.execute(select(PayrollPayment).where(PayrollPayment.payslip_id == slip.id))
                ).scalars().first()
                if payment and payment.status == "PENDING":
                    payment.status = "EXPORTED"
                    payment.exported_at = _now()
            body, mime = render_bank_file(settings.bank_format, export_rows)
        except Exception as exc:
            track_alert("payroll_bank_file_failed", run_id=str(run.id), error=type(exc).__name__)
            logger.exception("bank file failed")
            raise AppError("Could not build the bank transfer file", status_code=422) from exc
        await self._audit("bank.export", "payroll_run", run.id, None, {"rows": len(export_rows)}, None)
        return body, mime

    async def update_payment(self, payslip_id: UUID, data: dict) -> PayrollPayment:
        await self._allow(PREPARE)
        slip = await self.db.get(PayrollPayslip, payslip_id)
        if slip is None or slip.school_id != self.school_id:
            raise NotFoundError("Payslip not found")
        payment = (
            await self.db.execute(select(PayrollPayment).where(PayrollPayment.payslip_id == slip.id))
        ).scalars().first()
        if payment is None:
            payment = PayrollPayment(
                id=uuid4(), school_id=self.school_id, payslip_id=slip.id, amount=slip.net_amount, status="PENDING"
            )
            self.db.add(payment)
        status = data["status"].upper()
        if status not in {"PENDING", "EXPORTED", "PAID", "FAILED"}:
            raise AppError("Unknown payment status", status_code=422)
        before = {"status": payment.status}
        payment.status = status
        payment.utr = clean(data.get("utr"), max_len=64, field="UTR") if data.get("utr") else payment.utr
        payment.failure_reason = (
            clean(data.get("failure_reason"), max_len=500, field="failure reason") if data.get("failure_reason") else None
        )
        if status == "PAID":
            payment.paid_at = _now()
            slip.status = "PAID"
            slip.paid_on = date.today()
        await self._audit("payment.update", "payroll_payment", payment.id, before, {"status": status}, data.get("reason"))
        await self.db.flush()
        return payment

    async def settle(self, data: dict) -> PayrollSettlement:
        await self._allow(PREPARE)
        employee = await self._employee(data["employee_id"])
        settings = await self.ensure_settings()
        last_day = data["last_working_day"]
        assignment = await self._assignment(employee.id, last_day)
        basic = _ZERO
        da = _ZERO
        if assignment:
            for component in await self._resolved_components(assignment):
                if component.code == "BASIC":
                    basic = component.amount
                if component.code == "DA" and component.pf_wage:
                    da = component.amount
        years = completed_years(employee.joining_date, last_day)
        grat = gratuity(
            basic=basic,
            da=da,
            years=years,
            min_years=settings.gratuity_min_years,
            cap=settings.gratuity_cap,
            rounding=settings.rounding_mode,
        )
        encash_days = _d(data.get("leave_encashment_days") or 0)
        encash = (basic / Decimal("26") * encash_days) if basic else _ZERO
        notice = _d(data.get("notice_recovery") or 0)
        loans = list(
            (
                await self.db.execute(
                    select(PayrollLoan).where(PayrollLoan.employee_id == employee.id, PayrollLoan.status == "ACTIVE")
                )
            ).scalars()
        )
        loan_due = sum((loan.balance for loan in loans), _ZERO)
        net = grat + encash - notice - loan_due
        if net < 0:
            net = _ZERO
        row = PayrollSettlement(
            id=uuid4(),
            school_id=self.school_id,
            employee_id=employee.id,
            last_working_day=last_day,
            gratuity=grat,
            leave_encashment=encash,
            notice_recovery=notice,
            net=net,
            status="PENDING_APPROVAL",
            detail={"years": str(years), "loan_recovered": str(loan_due), "basic": str(basic)},
        )
        self.db.add(row)
        await self._audit("settlement.create", "settlement", row.id, None, row.detail, data.get("reason"))
        await self.db.flush()
        return row

    async def _run(self, run_id: UUID) -> PayrollRun:
        run = await self.db.get(PayrollRun, run_id)
        if run is None or run.school_id != self.school_id:
            raise NotFoundError("Payroll run not found")
        return run

    async def upload_logo(self, content: bytes, content_type: str | None) -> PayrollSchoolSettings:
        await self._allow(SETTINGS_WRITE)
        if len(content) > 1_000_000:
            raise AppError("Logo must be 1 MB or smaller", status_code=422)
        row = await self.ensure_settings()
        key = f"schools/{self.school_id}/payroll/logo"
        row.logo_object_key = put_object(key=key, data=content, content_type=content_type or "image/png")
        await self._audit("settings.logo", "payroll_settings", row.id, None, {"logo_object_key": row.logo_object_key}, "Logo updated")
        return row


def _bump_access(slip: PayrollPayslip, intent: str) -> None:
        kind = "view" if intent == "view" else "download"
        if kind == "view":
            slip.view_count = int(slip.view_count or 0) + 1
            if slip.first_viewed_at is None:
                slip.first_viewed_at = _now()
        else:
            slip.download_count = int(slip.download_count or 0) + 1
            if slip.first_downloaded_at is None:
                slip.first_downloaded_at = _now()


def _settings_for_pdf(row: PayrollSchoolSettings | None, school_id: UUID) -> PayrollSchoolSettings:
    if row is not None:
        return row
    return PayrollSchoolSettings(
        id=uuid4(),
        school_id=school_id,
        effective_from=date.today(),
        pt_state="KA",
        template_code="CLASSIC",
        primary_color="#1B4F72",
        accent_color="#148F77",
        show_employer_share=True,
        show_lop=True,
        show_bank_last4=True,
        show_pan=False,
    )


async def generate_locked_period(
    db: AsyncSession,
    *,
    school_id: UUID,
    actor_id: UUID,
    period_start: date,
    period_end: date,
    source: str = "ATTENDANCE_LOCK",
) -> dict:
    """Worker path. The attendance lock already authorised generation."""
    actor = CurrentUser(
        id=actor_id,
        email="payroll-worker@internal.sybeez.school",
        full_name="Payroll worker",
        role=UserRole.SCHOOL_ADMIN,
        school_id=school_id,
        membership_id=None,
        is_platform_admin=False,
    )
    return await PayrollService(db, actor, bypass=True).generate(
        period_start, period_end, source=source, reason="Attendance locked"
    )


def dump(row) -> dict | None:
    if row is None:
        return None
    return {column.name: _jsonable(getattr(row, column.name)) for column in row.__table__.columns}


def text_set(name: str, value: str):
    from sqlalchemy import text

    return text("SELECT set_config(:name, :value, true)"), {"name": f"app.{name}", "value": value}


def _settings_dict(row: PayrollSchoolSettings) -> dict:
    data = {}
    for column in row.__table__.columns:
        if column.name in {"id", "created_at", "updated_at"}:
            continue
        data[column.name] = getattr(row, column.name)
    return _jsonable(data)


def _apply_settings(row: PayrollSchoolSettings, data: dict) -> None:
    allowed = {
        "pt_state",
        "tax_regime_default",
        "pf_employee_rate",
        "pf_employer_rate",
        "pf_wage_ceiling",
        "pf_default_mode",
        "eps_rate",
        "esi_employee_rate",
        "esi_employer_rate",
        "esi_wage_ceiling",
        "rounding_mode",
        "lop_base",
        "pay_date_rule",
        "pay_day",
        "auto_publish",
        "bank_format",
        "template_code",
        "primary_color",
        "accent_color",
        "show_employer_share",
        "show_lop",
        "show_bank_last4",
        "show_pan",
        "shift_start",
        "shift_end",
        "grace_minutes",
        "full_day_minutes",
        "half_day_minutes",
        "geofence_enabled",
        "geofence_lat",
        "geofence_lng",
        "geofence_radius_m",
        "bonus_enabled",
        "bonus_percent",
        "bonus_wage_ceiling",
        "bonus_calc_ceiling",
        "bonus_month",
        "gratuity_cap",
        "gratuity_min_years",
        "require_different_approver",
        "effective_from",
    }
    if data.get("template_code") and data["template_code"].upper() not in TEMPLATES:
        raise AppError("template_code must be CLASSIC, COMPACT, or STATUTORY", status_code=422)
    if data.get("tax_regime_default") and data["tax_regime_default"].upper() not in {"NEW", "OLD"}:
        raise AppError("Default tax regime must be NEW or OLD", status_code=422)
    if data.get("pf_default_mode") and data["pf_default_mode"].upper() not in {"CAPPED", "FULL_BASIC"}:
        raise AppError("PF mode must be CAPPED or FULL_BASIC", status_code=422)
    if data.get("rounding_mode") and data["rounding_mode"].upper() not in {"NEAREST_RUPEE", "PAISE"}:
        raise AppError("Rounding must be NEAREST_RUPEE or PAISE", status_code=422)
    if data.get("lop_base") and data["lop_base"].upper() not in {"BASIC", "GROSS"}:
        raise AppError("LOP base must be BASIC or GROSS", status_code=422)
    if data.get("pay_date_rule") and data["pay_date_rule"].upper() not in {"LAST_WORKING_DAY", "FIXED_DAY"}:
        raise AppError("Pay date rule must be LAST_WORKING_DAY or FIXED_DAY", status_code=422)
    for key, value in data.items():
        if key not in allowed or value is None:
            continue
        if key in {"pt_state", "tax_regime_default", "pf_default_mode", "rounding_mode", "lop_base", "pay_date_rule", "bank_format", "template_code"}:
            value = str(value).upper()
        if key in {"primary_color", "accent_color"} and not (isinstance(value, str) and len(value) == 7 and value.startswith("#")):
            raise AppError("Colours must be #RRGGBB", status_code=422)
        if isinstance(value, str) and key not in {"shift_start", "shift_end", "effective_from"}:
            value = value.strip()
        setattr(row, key, value)
    if row.full_day_minutes < row.half_day_minutes:
        raise AppError("A full day cannot be shorter than a half day", status_code=422)
    if row.geofence_enabled and (row.geofence_lat is None or row.geofence_lng is None):
        raise AppError("A geofence needs a latitude and longitude", status_code=422)


def _pf_input(settings: PayrollSchoolSettings, profile: EmployeeStatutoryProfile | None, on: date) -> PfInput:
    enrolled = bool(profile and profile.pf_enrolled)
    mode = (profile.pf_wage_mode if profile and profile.pf_wage_mode else settings.pf_default_mode)
    return PfInput(
        enrolled=enrolled,
        basic=_ZERO,
        employee_rate=profile.pf_employee_rate if profile and profile.pf_employee_rate is not None else settings.pf_employee_rate,
        employer_rate=profile.pf_employer_rate if profile and profile.pf_employer_rate is not None else settings.pf_employer_rate,
        eps_rate=settings.eps_rate,
        ceiling=settings.pf_wage_ceiling,
        mode=mode,
        as_of=on,
        effective_from=profile.pf_effective_from if profile else None,
        effective_to=profile.pf_effective_to if profile else None,
        vpf_rate=profile.vpf_rate if profile else None,
        vpf_amount=profile.vpf_amount if profile else None,
        rounding=settings.rounding_mode,
    )


def _pay_date(settings, period_end: date, weekly, holidays) -> date:
    if settings.pay_date_rule == "FIXED_DAY" and settings.pay_day:
        import calendar

        last = calendar.monthrange(period_end.year, period_end.month)[1]
        return date(period_end.year, period_end.month, min(settings.pay_day, last))
    return last_working_day(period_end.year, period_end.month, weekly, holidays)


def _mask_bank(bank: EmployeeBankAccount | None) -> dict | None:
    if bank is None:
        return None
    return {
        "account_holder": bank.account_holder,
        "account_last4": bank.account_number[-4:],
        "ifsc": bank.ifsc,
        "bank_name": bank.bank_name,
    }


def _pay_dict(pay) -> dict:
    return {
        "working_days": str(pay.working_days),
        "lop_days": str(pay.lop_days),
        "paid_days": str(pay.paid_days),
        "gross": str(pay.gross),
        "deductions": str(pay.deductions),
        "net": str(pay.net),
        "lines": [
            {"code": line.code, "name": line.name, "kind": line.kind, "amount": str(line.amount)} for line in pay.lines
        ],
        "form16": pay.form16,
    }


def _pdf_payload(school, settings, run, slip, lines, employee, profile, bank) -> dict:
    def _bucket(kind):
        return [{"name": line.name, "amount": str(line.amount)} for line in lines if line.kind == kind]

    pan = profile.pan if profile else None
    if pan and not settings.show_pan:
        pan = "XXXXXX" + pan[-4:]
    return {
        "template_code": settings.template_code,
        "primary_color": settings.primary_color,
        "accent_color": settings.accent_color,
        "school_name": school.name if school else "Sybeez School",
        "period": f"{run.period_start.isoformat()} to {run.period_end.isoformat()}" if run else "",
        "employee_name": (employee.full_name if employee and employee.full_name else "Employee"),
        "employee_code": employee.employee_code if employee else None,
        "pan_masked": pan,
        "uan": profile.uan if profile else None,
        "tax_regime": slip.tax_regime,
        "pt_state": settings.pt_state,
        "show_bank_last4": settings.show_bank_last4,
        "account_last4": ("****" + bank.account_number[-4:]) if bank else None,
        "show_employer_share": settings.show_employer_share,
        "show_lop": settings.show_lop,
        "working_days": str(slip.working_days or ""),
        "lop_days": str(slip.lop_days or ""),
        "paid_days": str(slip.paid_days or ""),
        "earnings": _bucket("EARNING"),
        "deductions": _bucket("DEDUCTION"),
        "employer": _bucket("EMPLOYER"),
        "gross": str(slip.gross_amount),
        "deductions_amount": str(slip.deductions_amount),
        "net": str(slip.net_amount),
        "status": slip.status,
    }


# Silence unused import if a caller wants the admin attendance constant.
_ = ATTENDANCE_ADMIN
_tz = ZoneInfo
