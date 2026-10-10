"""School-scoped HR overview built from the HRM and Payroll tables; nothing is estimated."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError
from src.db.models import (
    Department,
    Employee,
    EmployeeDocument,
    HrLetter,
    HrRequest,
    HrWorkflow,
    HrWorkflowTask,
    PayrollPayslip,
    PayrollRun,
    School,
    StaffLeaveRequest,
    Teacher,
    User,
)
from src.db.models.hr import HR_REQUEST_OPEN_STATUSES, WORKFLOW_ACTIVE_STATUSES
from src.modules.employees.service import employee_active_expr, sync_teacher_employees
from src.modules.hr_dashboard.schemas import (
    AttendanceMonth,
    DepartmentHeadcount,
    Headcount,
    HrAlert,
    HrDashboardOut,
    LeaveSummary,
    RecordsSummary,
    SalarySlipSummary,
    WorkflowSummary,
)
from src.modules.hr_files.service import EmployeeDocumentService
from src.modules.staff_attendance.service import StaffAttendanceService
from src.shared.utils import utcnow


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


async def school_today(db: AsyncSession, school_id: UUID) -> tuple[date, str]:
    """Today in the school's timezone; UTC when the school has none or the host cannot resolve it."""
    tz_name = (await db.execute(select(School.timezone).where(School.id == school_id))).scalar_one_or_none()
    if tz_name:
        try:
            return datetime.now(ZoneInfo(tz_name)).date(), tz_name
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now(UTC).date(), "UTC"


class HrDashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def overview(self, school_id: UUID, as_of: date | None = None) -> HrDashboardOut:
        today, tz_name = await school_today(self.db, school_id)
        if as_of is not None:
            # The browser's local date may differ from the server's by a timezone offset, never more.
            if abs((as_of - datetime.now(UTC).date()).days) > 1:
                raise AppError("as_of must be today's date", status_code=422)
            today = as_of
        headcount, departments = await self._headcount(school_id)
        attendance = StaffAttendanceService(self.db)
        attendance_today = (await attendance.daily(school_id, today)).summary
        month_start = today.replace(day=1)
        month = await attendance.summary(school_id, date_from=month_start, date_to=today)
        leave = await self._leave(school_id, today)
        records = await self._records(school_id)
        workflows = await self._workflows(school_id, today)
        salary = await self._salary_slips(school_id)
        return HrDashboardOut(
            as_of=today,
            school_timezone=tz_name,
            generated_at=utcnow(),
            headcount=headcount,
            departments=departments,
            attendance_today=attendance_today,
            attendance_month=AttendanceMonth(date_from=month_start, date_to=today, totals=month.totals),
            leave=leave,
            records=records,
            workflows=workflows,
            salary_slips=salary,
            alerts=self._alerts(attendance_today.unmarked, attendance_today.total, leave, records, workflows),
        )

    async def _count(self, stmt) -> int:
        return int((await self.db.execute(stmt)).scalar_one() or 0)

    async def _headcount(self, school_id: UUID) -> tuple[Headcount, list[DepartmentHeadcount]]:
        await sync_teacher_employees(self.db, school_id)
        rows = (
            await self.db.execute(
                select(Employee.department_id, Department.name, Employee.staff_type, employee_active_expr())
                .outerjoin(Teacher, Teacher.id == Employee.teacher_id)
                .outerjoin(User, User.id == Teacher.user_id)
                .outerjoin(Department, Department.id == Employee.department_id)
                .where(Employee.school_id == school_id)
            )
        ).all()
        by_dept: dict[UUID | None, DepartmentHeadcount] = {}
        active = teaching = 0
        for dept_id, dept_name, staff_type, is_active in rows:
            entry = by_dept.setdefault(
                dept_id,
                DepartmentHeadcount(department_id=dept_id, name=dept_name or "No department", active=0, inactive=0),
            )
            if is_active:
                active += 1
                entry.active += 1
            else:
                entry.inactive += 1
            teaching += staff_type == "TEACHING"
        departments = sorted(by_dept.values(), key=lambda d: (d.department_id is None, -d.active, d.name.lower()))
        return (
            Headcount(
                total=len(rows),
                active=active,
                inactive=len(rows) - active,
                teaching=teaching,
                non_teaching=len(rows) - teaching,
            ),
            departments,
        )

    async def _leave(self, school_id: UUID, today: date) -> LeaveSummary:
        base = select(func.count()).select_from(StaffLeaveRequest).where(StaffLeaveRequest.school_id == school_id)
        approved = base.where(StaffLeaveRequest.status == "APPROVED")
        return LeaveSummary(
            pending=await self._count(base.where(StaffLeaveRequest.status == "PENDING")),
            on_leave_today=await self._count(
                approved.where(StaffLeaveRequest.start_date <= today, StaffLeaveRequest.end_date >= today)
            ),
            approved_next_7_days=await self._count(
                approved.where(StaffLeaveRequest.start_date.between(today + timedelta(days=1), today + timedelta(days=7)))
            ),
        )

    async def _records(self, school_id: UUID) -> RecordsSummary:
        letters = dict(
            (
                await self.db.execute(
                    select(HrLetter.status, func.count()).where(HrLetter.school_id == school_id).group_by(HrLetter.status)
                )
            ).all()
        )
        checklist = await EmployeeDocumentService(self.db).checklist(school_id)
        return RecordsSummary(
            open_requests=await self._count(
                select(func.count())
                .select_from(HrRequest)
                .where(HrRequest.school_id == school_id, HrRequest.status.in_(HR_REQUEST_OPEN_STATUSES))
            ),
            draft_letters=int(letters.get("DRAFT", 0)),
            issued_letters=int(letters.get("ISSUED", 0)),
            active_documents=await self._count(
                select(func.count())
                .select_from(EmployeeDocument)
                .where(EmployeeDocument.school_id == school_id, EmployeeDocument.status == "ACTIVE")
            ),
            checklist_required=list(checklist.required),
            checklist_complete=checklist.complete,
            checklist_incomplete=checklist.incomplete,
        )

    async def _workflows(self, school_id: UUID, today: date) -> WorkflowSummary:
        by_type = dict(
            (
                await self.db.execute(
                    select(HrWorkflow.workflow_type, func.count())
                    .where(HrWorkflow.school_id == school_id, HrWorkflow.status.in_(WORKFLOW_ACTIVE_STATUSES))
                    .group_by(HrWorkflow.workflow_type)
                )
            ).all()
        )
        total, done, overdue = (
            await self.db.execute(
                select(
                    func.count(HrWorkflowTask.id),
                    func.count(HrWorkflowTask.id).filter(HrWorkflowTask.is_done.is_(True)),
                    func.count(HrWorkflowTask.id).filter(
                        HrWorkflowTask.is_done.is_(False), HrWorkflowTask.due_date < today
                    ),
                )
                .join(HrWorkflow, HrWorkflow.id == HrWorkflowTask.workflow_id)
                .where(
                    HrWorkflow.school_id == school_id,
                    HrWorkflowTask.school_id == school_id,
                    HrWorkflow.status.in_(WORKFLOW_ACTIVE_STATUSES),
                )
            )
        ).one()
        return WorkflowSummary(
            onboarding_active=int(by_type.get("ONBOARDING", 0)),
            offboarding_active=int(by_type.get("OFFBOARDING", 0)),
            tasks_total=int(total or 0),
            tasks_done=int(done or 0),
            overdue_tasks=int(overdue or 0),
        )

    async def _salary_slips(self, school_id: UUID) -> SalarySlipSummary:
        periods = await self._count(
            select(func.count()).select_from(PayrollRun).where(PayrollRun.school_id == school_id)
        )
        latest = (
            await self.db.execute(
                select(PayrollRun)
                .where(PayrollRun.school_id == school_id)
                .order_by(PayrollRun.period_start.desc(), PayrollRun.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        slips = paid = 0
        if latest is not None:
            slips, paid = (
                await self.db.execute(
                    select(
                        func.count(PayrollPayslip.id).filter(PayrollPayslip.status != "CANCELLED"),
                        func.count(PayrollPayslip.id).filter(PayrollPayslip.status == "PAID"),
                    ).where(PayrollPayslip.school_id == school_id, PayrollPayslip.payroll_run_id == latest.id)
                )
            ).one()
        return SalarySlipSummary(
            pay_periods=periods,
            latest_label=latest.label if latest else None,
            latest_period_start=latest.period_start if latest else None,
            latest_period_end=latest.period_end if latest else None,
            latest_slips=int(slips or 0),
            latest_paid=int(paid or 0),
        )

    @staticmethod
    def _alerts(
        unmarked: int, roster: int, leave: LeaveSummary, records: RecordsSummary, workflows: WorkflowSummary
    ) -> list[HrAlert]:
        missing_docs = records.checklist_incomplete if records.checklist_required else 0
        candidates = [
            ("leave", "warning", leave.pending, "{} waiting for a decision", "leave request"),
            ("onboarding", "warning", workflows.overdue_tasks, "{} overdue", "onboarding or exit task"),
            ("documents", "warning", missing_docs, "{} missing required documents", "active employee"),
            ("requests", "info", records.open_requests, "{} open", "employee request"),
            ("attendance", "info", unmarked if roster else 0, "{} not yet marked today", "employee"),
            ("letters", "info", records.draft_letters, "{} not yet issued", "draft letter"),
        ]
        return [
            HrAlert(key=key, severity=severity, count=n, message=template.format(_plural(n, noun)))
            for key, severity, n, template, noun in candidates
            if n
        ]
