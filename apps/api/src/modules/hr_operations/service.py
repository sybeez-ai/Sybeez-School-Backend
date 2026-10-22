"""HR operations behind the release role and grant model.

School admin, HR, and HR_MANAGER (and a per-person hr.read / hr.manage grant) manage
these records. A teacher or staff member can read their own probation, appraisal,
training, roster, and issued assets, and can submit their own appraisal review.
The reporting manager named on the appraisal can submit the manager review.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    Department,
    Designation,
    Employee,
    EmployeeStatutoryProfile,
    PayrollLoan,
    PayrollLoanEvent,
    PayrollPayslip,
    PayrollRun,
    PayrollSettlement,
    StaffAttendance,
    StaffLeaveRequest,
    User,
)
from src.db.models.hr_operations import (
    HrApplication,
    HrAppraisal,
    HrAppraisalCycle,
    HrAppraisalGoal,
    HrAppraisalReview,
    HrAsset,
    HrAssetIssue,
    HrExitCase,
    HrInterview,
    HrJobOpening,
    HrOffer,
    HrProbation,
    HrReportExport,
    HrRosterEntry,
    HrSettlementLine,
    HrShift,
    HrTrainingProgram,
    HrTrainingRecord,
)
from src.modules.employees.schemas import EmployeeCreate, EmployeeExitIn
from src.modules.employees.service import EmployeeService
from src.modules.hr.access import user_is_hr_admin
from src.modules.hr.audit import hr_audit
from src.modules.hr_operations.export import to_csv, to_pdf, to_xlsx
from src.modules.hr_workflows.schemas import WorkflowCreate
from src.modules.hr_workflows.service import HrWorkflowService

_MONEY = Decimal("0.01")
_ZERO = Decimal("0.00")
_OPEN_PROBATION = ("ACTIVE", "EXTENDED")


def settlement_totals(lines: list[tuple[str, Decimal]]) -> tuple[Decimal, Decimal, Decimal]:
    """Dues, recoveries, and the payable amount (never negative)."""
    dues = _ZERO
    recoveries = _ZERO
    for kind, amount in lines:
        quantized = Decimal(amount).quantize(_MONEY, rounding=ROUND_HALF_UP)
        if kind == "DUE":
            dues += quantized
        elif kind == "RECOVERY":
            recoveries += quantized
    payable = dues - recoveries
    if payable < 0:
        payable = _ZERO
    return dues.quantize(_MONEY), recoveries.quantize(_MONEY), payable.quantize(_MONEY)


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def dump(row) -> dict:
    return {column.name: _plain(getattr(row, column.name)) for column in row.__table__.columns}


async def dumped(db: AsyncSession, row) -> dict:
    """Reload a flushed row before reading columns.

    A flush expires attributes on objects that were already loaded. Reading them
    from a sync helper would lazy-load and raise MissingGreenlet.
    """
    await db.refresh(row)
    return dump(row)


class HrOperations:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _employee(self, school_id: UUID, employee_id: UUID) -> Employee:
        row = await self.db.get(Employee, employee_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Employee not found")
        return row

    async def _own_employee(self, school_id: UUID, user_id: UUID) -> Employee:
        row = (
            await self.db.execute(
                select(Employee).where(Employee.school_id == school_id, Employee.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("No employee record is linked to this login")
        return row

    async def _manager_user_id(self, school_id: UUID, employee: Employee) -> UUID | None:
        if employee.reporting_manager_id is None:
            return None
        manager = await self._employee(school_id, employee.reporting_manager_id)
        return manager.user_id

    async def _audit(self, school_id: UUID, actor: UUID, action: str, resource: str, resource_id: UUID, message: str, details: dict | None = None) -> None:
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=actor,
            action=action,
            resource_type=resource,
            resource_id=resource_id,
            message=message,
            details=details,
        )

    # ── reports ────────────────────────────────────────────────────────────

    async def report(self, school_id: UUID, actor: UUID, kind: str, fmt: str, date_from: date | None, date_to: date | None):
        kind = kind.upper()
        fmt = fmt.upper()
        builders = {
            "HEADCOUNT": self._headcount,
            "ATTENDANCE": self._attendance,
            "LEAVE": self._leave,
            "ATTRITION": self._attrition,
            "DEPARTMENT": self._department,
            "STATUTORY": self._statutory,
        }
        if kind not in builders:
            raise AppError("Unknown report", status_code=422)
        if fmt not in {"JSON", "CSV", "XLSX", "PDF"}:
            raise AppError("Unknown format", status_code=422)
        headers, rows = await builders[kind](school_id, date_from, date_to)
        export = HrReportExport(
            id=uuid4(),
            school_id=school_id,
            kind=kind,
            format=fmt,
            filters={"date_from": date_from.isoformat() if date_from else None, "date_to": date_to.isoformat() if date_to else None},
            row_count=len(rows),
            created_by=actor,
        )
        self.db.add(export)
        await self._audit(school_id, actor, "hr.report.export", "hr_report_export", export.id, f"{kind} report ({fmt})", {"rows": len(rows)})
        await self.db.flush()
        title = f"{kind.title()} report"
        if fmt == "CSV":
            return to_csv(headers, rows), "text/csv", f"{kind.lower()}.csv"
        if fmt == "XLSX":
            return (
                to_xlsx(headers, rows),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                f"{kind.lower()}.xlsx",
            )
        if fmt == "PDF":
            return to_pdf(title, headers, rows), "application/pdf", f"{kind.lower()}.pdf"
        return {"kind": kind, "columns": headers, "rows": [dict(zip(headers, row, strict=True)) for row in rows], "row_count": len(rows)}

    async def _names(self, school_id: UUID):
        rows = (
            await self.db.execute(
                select(Employee, Department.name, Designation.name, User.full_name)
                .outerjoin(Department, Department.id == Employee.department_id)
                .outerjoin(Designation, Designation.id == Employee.designation_id)
                .outerjoin(User, User.id == Employee.user_id)
                .where(Employee.school_id == school_id)
                .order_by(Employee.created_at)
            )
        ).all()
        return rows

    @staticmethod
    def _person(employee: Employee, department: str | None, designation: str | None, login_name: str | None) -> list:
        return [
            str(employee.id),
            employee.full_name or login_name or "",
            employee.employee_code or "",
            employee.staff_type,
            department or "",
            designation or "",
            employee.status,
            employee.joining_date.isoformat() if employee.joining_date else "",
        ]

    async def _headcount(self, school_id: UUID, _start, _end):
        headers = ["employee_id", "name", "code", "staff_type", "department", "designation", "status", "joining_date"]
        return headers, [self._person(*row) for row in await self._names(school_id)]

    async def _department(self, school_id: UUID, _start, _end):
        headers, rows = await self._headcount(school_id, None, None)
        rows.sort(key=lambda row: (row[4], row[1]))
        return headers, rows

    async def _attrition(self, school_id: UUID, start: date | None, end: date | None):
        headers = ["employee_id", "name", "code", "staff_type", "department", "designation", "exit_date", "exit_reason"]
        rows = []
        for employee, department, designation, login_name in await self._names(school_id):
            if employee.exit_date is None:
                continue
            if start and employee.exit_date < start:
                continue
            if end and employee.exit_date > end:
                continue
            person = self._person(employee, department, designation, login_name)
            rows.append(person[:6] + [employee.exit_date.isoformat(), employee.exit_reason or ""])
        return headers, rows

    async def _attendance(self, school_id: UUID, start: date | None, end: date | None):
        stmt = (
            select(StaffAttendance, Employee.full_name, User.full_name)
            .join(Employee, Employee.id == StaffAttendance.employee_id)
            .outerjoin(User, User.id == Employee.user_id)
            .where(StaffAttendance.school_id == school_id)
            .order_by(StaffAttendance.attendance_date)
        )
        if start:
            stmt = stmt.where(StaffAttendance.attendance_date >= start)
        if end:
            stmt = stmt.where(StaffAttendance.attendance_date <= end)
        rows = []
        for mark, full_name, login_name in (await self.db.execute(stmt)).all():
            rows.append([
                str(mark.employee_id),
                full_name or login_name or "",
                mark.attendance_date.isoformat(),
                mark.status,
            ])
        return ["employee_id", "name", "date", "status"], rows

    async def _leave(self, school_id: UUID, start: date | None, end: date | None):
        stmt = (
            select(StaffLeaveRequest, Employee.full_name, User.full_name)
            .join(User, User.id == StaffLeaveRequest.user_id)
            .outerjoin(Employee, (Employee.user_id == StaffLeaveRequest.user_id) & (Employee.school_id == school_id))
            .where(StaffLeaveRequest.school_id == school_id)
            .order_by(StaffLeaveRequest.start_date)
        )
        if start:
            stmt = stmt.where(StaffLeaveRequest.end_date >= start)
        if end:
            stmt = stmt.where(StaffLeaveRequest.start_date <= end)
        rows = []
        for request, full_name, login_name in (await self.db.execute(stmt)).all():
            rows.append([
                str(request.user_id),
                full_name or login_name or "",
                request.start_date.isoformat(),
                request.end_date.isoformat(),
                request.status,
                str(request.working_days or ""),
            ])
        return ["user_id", "name", "start_date", "end_date", "status", "working_days"], rows

    async def _statutory(self, school_id: UUID, _start, _end):
        stmt = (
            select(Employee, User.full_name, EmployeeStatutoryProfile)
            .outerjoin(User, User.id == Employee.user_id)
            .outerjoin(EmployeeStatutoryProfile, EmployeeStatutoryProfile.employee_id == Employee.id)
            .where(Employee.school_id == school_id)
            .order_by(Employee.created_at)
        )
        rows = []
        for employee, login_name, profile in (await self.db.execute(stmt)).all():
            rows.append([
                str(employee.id),
                employee.full_name or login_name or "",
                employee.employee_code or "",
                profile.pan if profile else "",
                profile.uan if profile else "",
                profile.pf_number if profile else "",
                profile.esi_number if profile else "",
                "yes" if profile and profile.pf_enrolled else "no",
                "yes" if profile and profile.esi_enrolled else "no",
            ])
        return ["employee_id", "name", "code", "pan", "uan", "pf_number", "esi_number", "pf_enrolled", "esi_enrolled"], rows

    # ── probation ──────────────────────────────────────────────────────────

    async def start_probation(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        employee = await self._employee(school_id, data["employee_id"])
        if data["end_date"] < data["start_date"]:
            raise AppError("Probation end date cannot be before the start date", status_code=422)
        open_row = (
            await self.db.execute(
                select(HrProbation).where(
                    HrProbation.employee_id == employee.id,
                    HrProbation.status.in_(_OPEN_PROBATION),
                )
            )
        ).scalar_one_or_none()
        if open_row is not None:
            raise ConflictError("This employee already has an open probation")
        row = HrProbation(
            id=uuid4(),
            school_id=school_id,
            employee_id=employee.id,
            subject_user_id=employee.user_id,
            start_date=data["start_date"],
            end_date=data["end_date"],
            status="ACTIVE",
            notes=data.get("notes"),
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.probation.start", "hr_probation", row.id, "Probation started")
        await self.db.flush()
        return await dumped(self.db, row)

    async def decide_probation(self, school_id: UUID, actor: UUID, probation_id: UUID, data: dict) -> dict:
        row = await self.db.get(HrProbation, probation_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Probation not found")
        if row.status not in _OPEN_PROBATION:
            raise ConflictError("This probation is already decided")
        action = data["action"].upper()
        if action == "EXTEND":
            if not data.get("extended_until") or data["extended_until"] <= row.end_date:
                raise AppError("Extended date must be after the current end date", status_code=422)
            row.status = "EXTENDED"
            row.extended_until = data["extended_until"]
            row.end_date = data["extended_until"]
        elif action == "CONFIRM":
            row.status = "CONFIRMED"
            row.confirmed_on = data.get("confirmed_on") or date.today()
        elif action == "TERMINATE":
            row.status = "TERMINATED"
        else:
            raise AppError("Unknown probation action", status_code=422)
        row.notes = data.get("notes") or row.notes
        row.decided_by = actor
        await self._audit(school_id, actor, f"hr.probation.{action.lower()}", "hr_probation", row.id, f"Probation {action.lower()}")
        await self.db.flush()
        return await dumped(self.db, row)

    async def list_probation(self, school_id: UUID, *, user_id: UUID | None = None) -> list[dict]:
        stmt = select(HrProbation).where(HrProbation.school_id == school_id).order_by(HrProbation.created_at.desc())
        if user_id is not None:
            employee = await self._own_employee(school_id, user_id)
            stmt = stmt.where(HrProbation.employee_id == employee.id)
        return [dump(row) for row in (await self.db.execute(stmt)).scalars()]

    # ── appraisals ─────────────────────────────────────────────────────────

    async def create_cycle(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        if data["period_end"] < data["period_start"]:
            raise AppError("Cycle end cannot be before the start", status_code=422)
        row = HrAppraisalCycle(
            id=uuid4(),
            school_id=school_id,
            name=data["name"].strip(),
            period_start=data["period_start"],
            period_end=data["period_end"],
            status="DRAFT",
            created_by=actor,
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.appraisal.cycle", "hr_appraisal_cycle", row.id, row.name)
        await self.db.flush()
        return await dumped(self.db, row)

    async def open_cycle(self, school_id: UUID, actor: UUID, cycle_id: UUID) -> dict:
        cycle = await self._cycle(school_id, cycle_id)
        if cycle.status == "CLOSED":
            raise ConflictError("A closed cycle cannot be opened")
        cycle.status = "OPEN"
        employees = (
            await self.db.execute(
                select(Employee).where(Employee.school_id == school_id, Employee.status == "ACTIVE")
            )
        ).scalars()
        for employee in employees:
            existing = (
                await self.db.execute(
                    select(HrAppraisal).where(HrAppraisal.cycle_id == cycle.id, HrAppraisal.employee_id == employee.id)
                )
            ).scalar_one_or_none()
            if existing is None:
                self.db.add(
                    HrAppraisal(
                        id=uuid4(),
                        school_id=school_id,
                        cycle_id=cycle.id,
                        employee_id=employee.id,
                        subject_user_id=employee.user_id,
                        manager_user_id=await self._manager_user_id(school_id, employee),
                        status="OPEN",
                    )
                )
        await self._audit(school_id, actor, "hr.appraisal.open", "hr_appraisal_cycle", cycle.id, "Cycle opened")
        await self.db.flush()
        return await dumped(self.db, cycle)

    async def add_goal(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        cycle = await self._cycle(school_id, data["cycle_id"])
        employee = await self._employee(school_id, data["employee_id"])
        row = HrAppraisalGoal(
            id=uuid4(),
            school_id=school_id,
            cycle_id=cycle.id,
            employee_id=employee.id,
            subject_user_id=employee.user_id,
            manager_user_id=await self._manager_user_id(school_id, employee),
            title=data["title"].strip(),
            description=data.get("description"),
            weight=Decimal(data.get("weight") or 0),
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.appraisal.goal", "hr_appraisal_goal", row.id, row.title)
        await self.db.flush()
        return await dumped(self.db, row)

    async def list_appraisals(self, school_id: UUID, *, user_id: UUID | None = None) -> list[dict]:
        stmt = select(HrAppraisal).where(HrAppraisal.school_id == school_id).order_by(HrAppraisal.created_at.desc())
        if user_id is not None:
            employee = await self._own_employee(school_id, user_id)
            stmt = stmt.where(
                (HrAppraisal.employee_id == employee.id) | (HrAppraisal.manager_user_id == user_id)
            )
        return [dump(row) for row in (await self.db.execute(stmt)).scalars()]

    async def review(self, school_id: UUID, user: CurrentUser, appraisal_id: UUID, kind: str, data: dict) -> dict:
        appraisal = await self._appraisal(school_id, appraisal_id)
        kind = kind.upper()
        if kind == "SELF":
            if appraisal.subject_user_id != user.id:
                raise ForbiddenError("Only the employee can submit the self review")
            if appraisal.status not in {"OPEN", "SELF_SUBMITTED"}:
                raise ConflictError("Self review is closed")
            appraisal.status = "SELF_SUBMITTED"
        elif kind == "MANAGER":
            is_admin = await user_is_hr_admin(self.db, user, write=True)
            if not is_admin and appraisal.manager_user_id != user.id:
                raise ForbiddenError("Only the reporting manager or HR can record this review")
            if appraisal.status not in {"SELF_SUBMITTED", "MANAGER_REVIEWED"}:
                raise ConflictError("Self review must be submitted first")
            appraisal.status = "MANAGER_REVIEWED"
        else:
            raise AppError("Unknown review kind", status_code=422)
        existing = (
            await self.db.execute(
                select(HrAppraisalReview).where(
                    HrAppraisalReview.appraisal_id == appraisal.id,
                    HrAppraisalReview.kind == kind,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = HrAppraisalReview(
                id=uuid4(),
                school_id=school_id,
                appraisal_id=appraisal.id,
                employee_id=appraisal.employee_id,
                subject_user_id=appraisal.subject_user_id,
                kind=kind,
                rating=Decimal(data["rating"]),
            )
            self.db.add(existing)
        existing.reviewer_user_id = user.id
        existing.rating = Decimal(data["rating"])
        existing.comments = data.get("comments")
        await self._audit(school_id, user.id, f"hr.appraisal.{kind.lower()}", "hr_appraisal", appraisal.id, f"{kind.title()} review")
        await self.db.flush()
        return await dumped(self.db, existing)

    async def finalise(self, school_id: UUID, actor: UUID, appraisal_id: UUID, data: dict) -> dict:
        appraisal = await self._appraisal(school_id, appraisal_id)
        if appraisal.status not in {"MANAGER_REVIEWED", "FINAL"}:
            raise ConflictError("Manager review must be recorded first")
        appraisal.status = "FINAL"
        appraisal.final_rating = Decimal(data["rating"])
        appraisal.final_comments = data.get("comments")
        await self._audit(school_id, actor, "hr.appraisal.final", "hr_appraisal", appraisal.id, "Appraisal finalised")
        await self.db.flush()
        return await dumped(self.db, appraisal)

    async def _cycle(self, school_id: UUID, cycle_id: UUID) -> HrAppraisalCycle:
        row = await self.db.get(HrAppraisalCycle, cycle_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Appraisal cycle not found")
        return row

    async def _appraisal(self, school_id: UUID, appraisal_id: UUID) -> HrAppraisal:
        row = await self.db.get(HrAppraisal, appraisal_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Appraisal not found")
        return row

    # ── recruitment ────────────────────────────────────────────────────────

    async def create_opening(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        row = HrJobOpening(
            id=uuid4(),
            school_id=school_id,
            title=data["title"].strip(),
            department_id=data.get("department_id"),
            designation_id=data.get("designation_id"),
            description=data.get("description"),
            openings=data.get("openings") or 1,
            status="OPEN" if data.get("publish") else "DRAFT",
            created_by=actor,
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.recruitment.opening", "hr_job_opening", row.id, row.title)
        await self.db.flush()
        return await dumped(self.db, row)

    async def add_application(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        opening = await self.db.get(HrJobOpening, data["opening_id"])
        if opening is None or opening.school_id != school_id:
            raise NotFoundError("Job opening not found")
        if opening.status != "OPEN":
            raise ConflictError("This opening is not accepting applications")
        row = HrApplication(
            id=uuid4(),
            school_id=school_id,
            opening_id=opening.id,
            applicant_name=data["applicant_name"].strip(),
            email=str(data["email"]).lower() if data.get("email") else None,
            phone=data.get("phone"),
            status="APPLIED",
            created_by=actor,
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.recruitment.apply", "hr_application", row.id, row.applicant_name)
        await self.db.flush()
        return await dumped(self.db, row)

    async def add_interview(self, school_id: UUID, actor: UUID, application_id: UUID, data: dict) -> dict:
        application = await self._application(school_id, application_id)
        if application.status in {"HIRED", "REJECTED", "WITHDRAWN"}:
            raise ConflictError("This application is closed")
        row = HrInterview(
            id=uuid4(),
            school_id=school_id,
            application_id=application.id,
            stage=data["stage"].upper(),
            scheduled_at=data.get("scheduled_at"),
            interviewer_employee_id=data.get("interviewer_employee_id"),
            outcome=(data.get("outcome") or "SCHEDULED").upper(),
            notes=data.get("notes"),
        )
        application.status = "INTERVIEW"
        self.db.add(row)
        await self._audit(school_id, actor, "hr.recruitment.interview", "hr_interview", row.id, row.stage)
        await self.db.flush()
        return await dumped(self.db, row)

    async def make_offer(self, school_id: UUID, actor: UUID, application_id: UUID, data: dict) -> dict:
        application = await self._application(school_id, application_id)
        if application.status in {"HIRED", "REJECTED", "WITHDRAWN"}:
            raise ConflictError("This application is closed")
        existing = (
            await self.db.execute(select(HrOffer).where(HrOffer.application_id == application.id))
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("An offer already exists for this application")
        row = HrOffer(
            id=uuid4(),
            school_id=school_id,
            application_id=application.id,
            offered_on=data["offered_on"],
            joining_date=data.get("joining_date"),
            salary_note=data.get("salary_note"),
            status="SENT",
        )
        application.status = "OFFERED"
        self.db.add(row)
        await self._audit(school_id, actor, "hr.recruitment.offer", "hr_offer", row.id, "Offer sent")
        await self.db.flush()
        return await dumped(self.db, row)

    async def accept_offer(self, school_id: UUID, actor: UUID, offer_id: UUID) -> dict:
        offer = await self.db.get(HrOffer, offer_id)
        if offer is None or offer.school_id != school_id:
            raise NotFoundError("Offer not found")
        if offer.status != "SENT":
            raise ConflictError("Only a sent offer can be accepted")
        offer.status = "ACCEPTED"
        await self._audit(school_id, actor, "hr.recruitment.accept", "hr_offer", offer.id, "Offer accepted")
        await self.db.flush()
        return await dumped(self.db, offer)

    async def hire(self, school_id: UUID, actor: UUID, application_id: UUID, data: dict) -> dict:
        application = await self._application(school_id, application_id)
        offer = (
            await self.db.execute(select(HrOffer).where(HrOffer.application_id == application.id))
        ).scalar_one_or_none()
        if offer is None or offer.status != "ACCEPTED":
            raise ConflictError("Accept the offer before hiring")
        opening = await self.db.get(HrJobOpening, application.opening_id)
        joining = data.get("joining_date") or (offer.joining_date if offer else None) or date.today()
        employee = await EmployeeService(self.db).create(
            school_id,
            actor,
            EmployeeCreate(
                staff_type=data["staff_type"],
                full_name=application.applicant_name,
                email=application.email,
                phone=application.phone,
                joining_date=joining,
                department_id=opening.department_id if opening else None,
                designation_id=opening.designation_id if opening else None,
            ),
        )
        workflow = await HrWorkflowService(self.db).start(
            school_id,
            actor,
            WorkflowCreate(employee_id=employee.id, workflow_type="ONBOARDING", start_date=joining),
        )
        application.status = "HIRED"
        application.employee_id = employee.id
        await self._audit(
            school_id,
            actor,
            "hr.recruitment.hire",
            "hr_application",
            application.id,
            f"{application.applicant_name} hired",
            {"employee_id": str(employee.id), "workflow_id": str(workflow.id)},
        )
        await self.db.flush()
        return {
            "application": await dumped(self.db, application),
            "employee_id": str(employee.id),
            "workflow_id": str(workflow.id),
        }

    async def _application(self, school_id: UUID, application_id: UUID) -> HrApplication:
        row = await self.db.get(HrApplication, application_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Application not found")
        return row

    # ── training ───────────────────────────────────────────────────────────

    async def create_program(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        row = HrTrainingProgram(
            id=uuid4(),
            school_id=school_id,
            title=data["title"].strip(),
            provider=data.get("provider"),
            description=data.get("description"),
            valid_months=data.get("valid_months"),
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.training.program", "hr_training_program", row.id, row.title)
        await self.db.flush()
        return await dumped(self.db, row)

    async def assign_training(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        program = await self.db.get(HrTrainingProgram, data["program_id"])
        if program is None or program.school_id != school_id:
            raise NotFoundError("Training program not found")
        employee = await self._employee(school_id, data["employee_id"])
        completed = data.get("completed_on")
        expires = data.get("expires_on")
        status = "COMPLETED" if completed else "ASSIGNED"
        if expires and expires < date.today() and completed:
            status = "EXPIRED"
        row = HrTrainingRecord(
            id=uuid4(),
            school_id=school_id,
            program_id=program.id,
            employee_id=employee.id,
            subject_user_id=employee.user_id,
            completed_on=completed,
            certificate_no=data.get("certificate_no"),
            expires_on=expires,
            status=status,
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.training.assign", "hr_training_record", row.id, program.title)
        await self.db.flush()
        return await dumped(self.db, row)

    async def my_training(self, school_id: UUID, user_id: UUID) -> list[dict]:
        employee = await self._own_employee(school_id, user_id)
        rows = (
            await self.db.execute(
                select(HrTrainingRecord).where(HrTrainingRecord.employee_id == employee.id).order_by(HrTrainingRecord.created_at.desc())
            )
        ).scalars()
        return [dump(row) for row in rows]

    # ── roster ─────────────────────────────────────────────────────────────

    async def create_shift(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        row = HrShift(
            id=uuid4(),
            school_id=school_id,
            name=data["name"].strip(),
            start_time=data["start_time"],
            end_time=data["end_time"],
            break_minutes=data.get("break_minutes") or 0,
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.roster.shift", "hr_shift", row.id, row.name)
        await self.db.flush()
        return await dumped(self.db, row)

    async def list_shifts(self, school_id: UUID) -> list[dict]:
        stmt = select(HrShift).where(HrShift.school_id == school_id).order_by(HrShift.start_time, HrShift.name)
        return [dump(row) for row in (await self.db.execute(stmt)).scalars()]

    async def assign_roster(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        employee = await self._employee(school_id, data["employee_id"])
        shift = await self.db.get(HrShift, data["shift_id"])
        if shift is None or shift.school_id != school_id:
            raise NotFoundError("Shift not found")
        row = HrRosterEntry(
            id=uuid4(),
            school_id=school_id,
            employee_id=employee.id,
            subject_user_id=employee.user_id,
            shift_id=shift.id,
            duty_date=data["duty_date"],
            duty=data.get("duty") or "DUTY",
            timetable_period=data.get("timetable_period"),
            overtime_minutes=data.get("overtime_minutes") or 0,
            notes=data.get("notes"),
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.roster.assign", "hr_roster_entry", row.id, f"{row.duty} {row.duty_date}")
        await self.db.flush()
        return await dumped(self.db, row)

    async def list_roster(self, school_id: UUID, *, user_id: UUID | None = None, date_from: date | None = None, date_to: date | None = None) -> list[dict]:
        stmt = select(HrRosterEntry).where(HrRosterEntry.school_id == school_id).order_by(HrRosterEntry.duty_date)
        if user_id is not None:
            employee = await self._own_employee(school_id, user_id)
            stmt = stmt.where(HrRosterEntry.employee_id == employee.id)
        if date_from:
            stmt = stmt.where(HrRosterEntry.duty_date >= date_from)
        if date_to:
            stmt = stmt.where(HrRosterEntry.duty_date <= date_to)
        return [dump(row) for row in (await self.db.execute(stmt)).scalars()]

    async def overtime(self, school_id: UUID, date_from: date | None, date_to: date | None) -> list[dict]:
        stmt = (
            select(HrRosterEntry.employee_id, func.sum(HrRosterEntry.overtime_minutes))
            .where(HrRosterEntry.school_id == school_id)
            .group_by(HrRosterEntry.employee_id)
        )
        if date_from:
            stmt = stmt.where(HrRosterEntry.duty_date >= date_from)
        if date_to:
            stmt = stmt.where(HrRosterEntry.duty_date <= date_to)
        return [
            {"employee_id": str(employee_id), "overtime_minutes": int(minutes or 0)}
            for employee_id, minutes in (await self.db.execute(stmt)).all()
        ]

    # ── assets ─────────────────────────────────────────────────────────────

    async def create_asset(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        row = HrAsset(
            id=uuid4(),
            school_id=school_id,
            name=data["name"].strip(),
            asset_tag=data["asset_tag"].strip(),
            category=data.get("category"),
            serial_no=data.get("serial_no"),
            cost=Decimal(data.get("cost") or 0),
            status="IN_STOCK",
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.asset.create", "hr_asset", row.id, row.asset_tag)
        await self.db.flush()
        return await dumped(self.db, row)

    async def issue_asset(self, school_id: UUID, actor: UUID, asset_id: UUID, data: dict) -> dict:
        asset = await self.db.get(HrAsset, asset_id)
        if asset is None or asset.school_id != school_id:
            raise NotFoundError("Asset not found")
        if asset.status != "IN_STOCK":
            raise ConflictError("This asset is not available to issue")
        employee = await self._employee(school_id, data["employee_id"])
        asset.status = "ISSUED"
        row = HrAssetIssue(
            id=uuid4(),
            school_id=school_id,
            asset_id=asset.id,
            employee_id=employee.id,
            subject_user_id=employee.user_id,
            asset_name=asset.name,
            asset_tag=asset.asset_tag,
            issued_on=data["issued_on"],
            condition_out=data.get("condition_out"),
            notes=data.get("notes"),
        )
        self.db.add(row)
        await self._audit(school_id, actor, "hr.asset.issue", "hr_asset_issue", row.id, asset.asset_tag)
        await self.db.flush()
        return await dumped(self.db, row)

    async def return_asset(self, school_id: UUID, actor: UUID, issue_id: UUID, data: dict) -> dict:
        row = await self.db.get(HrAssetIssue, issue_id)
        if row is None or row.school_id != school_id:
            raise NotFoundError("Asset issue not found")
        if row.returned_on is not None:
            raise ConflictError("This asset is already returned")
        row.returned_on = data["returned_on"]
        row.condition_in = data.get("condition_in")
        asset = await self.db.get(HrAsset, row.asset_id)
        if asset is not None:
            asset.status = "RETURNED" if data.get("write_off") else "IN_STOCK"
            if data.get("write_off"):
                asset.status = "WRITTEN_OFF"
        await self._audit(school_id, actor, "hr.asset.return", "hr_asset_issue", row.id, row.asset_tag)
        await self.db.flush()
        return await dumped(self.db, row)

    async def my_assets(self, school_id: UUID, user_id: UUID) -> list[dict]:
        employee = await self._own_employee(school_id, user_id)
        rows = (
            await self.db.execute(
                select(HrAssetIssue).where(
                    HrAssetIssue.employee_id == employee.id,
                    HrAssetIssue.returned_on.is_(None),
                )
            )
        ).scalars()
        return [dump(row) for row in rows]

    # ── exit settlement ────────────────────────────────────────────────────

    async def draft_exit(self, school_id: UUID, actor: UUID, data: dict) -> dict:
        employee = await self._employee(school_id, data["employee_id"])
        if employee.status == "LEFT":
            raise ConflictError("This employee has already left")
        supplied = [
            (item["kind"].upper(), Decimal(item["amount"]), item["label"].strip())
            for item in data.get("lines") or []
        ]
        loans = list(
            (
                await self.db.execute(
                    select(PayrollLoan).where(
                        PayrollLoan.employee_id == employee.id,
                        PayrollLoan.status == "ACTIVE",
                        PayrollLoan.balance > 0,
                    )
                )
            ).scalars()
        )
        assets = (
            await self.db.execute(
                select(HrAsset)
                .join(HrAssetIssue, HrAssetIssue.asset_id == HrAsset.id)
                .where(
                    HrAssetIssue.employee_id == employee.id,
                    HrAssetIssue.returned_on.is_(None),
                    HrAsset.cost > 0,
                )
            )
        ).scalars()
        automatic = [("RECOVERY", loan.balance, "Outstanding loan") for loan in loans]
        automatic += [("RECOVERY", asset.cost, f"Unreturned asset {asset.asset_tag}") for asset in assets]
        lines = supplied + automatic
        dues, recoveries, payable = settlement_totals([(kind, amount) for kind, amount, _label in lines])
        case = HrExitCase(
            id=uuid4(),
            school_id=school_id,
            employee_id=employee.id,
            exit_date=data["exit_date"],
            exit_reason=data["exit_reason"],
            status="DRAFT",
            dues_total=dues,
            recoveries_total=recoveries,
            payable=payable,
            notes=data.get("notes"),
            created_by=actor,
        )
        self.db.add(case)
        await self.db.flush()
        stored = []
        for kind, amount, label in lines:
            line = HrSettlementLine(
                id=uuid4(),
                school_id=school_id,
                case_id=case.id,
                kind=kind,
                label=label,
                amount=Decimal(amount).quantize(_MONEY, rounding=ROUND_HALF_UP),
            )
            self.db.add(line)
            stored.append(line)
        await self._audit(school_id, actor, "hr.exit.draft", "hr_exit_case", case.id, "Settlement drafted", {"payable": str(payable)})
        await self.db.flush()
        return {
            **await dumped(self.db, case),
            "lines": [await dumped(self.db, line) for line in stored],
        }

    async def post_exit(self, school_id: UUID, actor: UUID, case_id: UUID) -> dict:
        case = await self.db.get(HrExitCase, case_id)
        if case is None or case.school_id != school_id:
            raise NotFoundError("Exit case not found")
        if case.status != "DRAFT":
            raise ConflictError("This settlement is already posted")
        employee = await self._employee(school_id, case.employee_id)
        run = PayrollRun(
            id=uuid4(),
            school_id=school_id,
            label=f"Full and final {case.exit_date.isoformat()}",
            period_start=case.exit_date,
            period_end=case.exit_date,
            status="FINALIZED",
            run_kind="SETTLEMENT",
            approval_status="APPROVED",
            source="HR_EXIT",
            created_by=actor,
            pay_date=case.exit_date,
        )
        self.db.add(run)
        await self.db.flush()
        slip = PayrollPayslip(
            id=uuid4(),
            school_id=school_id,
            payroll_run_id=run.id,
            teacher_id=employee.teacher_id,
            employee_id=employee.id,
            gross_amount=case.dues_total,
            deductions_amount=case.recoveries_total,
            net_amount=case.payable,
            status="PUBLISHED",
            notes="Full and final settlement",
        )
        self.db.add(slip)
        settlement = PayrollSettlement(
            id=uuid4(),
            school_id=school_id,
            employee_id=employee.id,
            payroll_run_id=run.id,
            last_working_day=case.exit_date,
            gratuity=_ZERO,
            leave_encashment=_ZERO,
            notice_recovery=_ZERO,
            net=case.payable,
            status="APPROVED",
            detail={
                "dues": str(case.dues_total),
                "recoveries": str(case.recoveries_total),
                "payable": str(case.payable),
                "exit_case_id": str(case.id),
            },
        )
        self.db.add(settlement)
        loans = (
            await self.db.execute(
                select(PayrollLoan).where(PayrollLoan.employee_id == employee.id, PayrollLoan.status == "ACTIVE")
            )
        ).scalars()
        for loan in loans:
            self.db.add(
                PayrollLoanEvent(
                    id=uuid4(),
                    school_id=school_id,
                    loan_id=loan.id,
                    payslip_id=slip.id,
                    kind="CLOSE",
                    principal=loan.balance,
                    interest=_ZERO,
                    balance_after=_ZERO,
                    note="Recovered in full and final settlement",
                )
            )
            loan.balance = _ZERO
            loan.status = "CLOSED"
        assets = (
            await self.db.execute(
                select(HrAsset)
                .join(HrAssetIssue, HrAssetIssue.asset_id == HrAsset.id)
                .where(HrAssetIssue.employee_id == employee.id, HrAssetIssue.returned_on.is_(None))
            )
        ).scalars()
        for asset in assets:
            asset.status = "WRITTEN_OFF"
        case.status = "POSTED"
        case.payroll_run_id = run.id
        case.payslip_id = slip.id
        case.payroll_settlement_id = settlement.id
        await self.db.flush()
        await EmployeeService(self.db).exit(
            school_id,
            actor,
            employee.id,
            EmployeeExitIn(exit_date=case.exit_date, exit_reason=case.exit_reason, notes=case.notes),
        )
        await self._audit(
            school_id,
            actor,
            "hr.exit.post",
            "hr_exit_case",
            case.id,
            "Settlement posted to payroll",
            {"payroll_run_id": str(run.id), "payslip_id": str(slip.id), "payable": str(case.payable)},
        )
        await self.db.flush()
        return await dumped(self.db, case)
