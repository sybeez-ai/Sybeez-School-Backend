from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    Department,
    Employee,
    HrLeaveType,
    PayrollPayslip,
    PayrollRun,
    School,
    StaffAttendance,
    StaffLeaveRequest,
    Teacher,
    User,
)
from src.modules.employees.lookup import employee_for_user
from src.modules.employees.service import employee_code_expr
from src.modules.hr.audit import hr_audit
from src.modules.hr.payslip_pdf import build_payslip_pdf
from src.modules.hr.schemas import (
    PayrollPayslipCreate,
    PayrollPayslipOut,
    PayrollPayslipUpdate,
    PayrollRunCreate,
    PayrollRunDetailOut,
    PayrollRunOut,
    StaffLeaveDecisionIn,
    StaffLeaveRequestCreate,
    StaffLeaveRequestOut,
    StaffLeaveRevokeIn,
)
from src.modules.hr_leave_policy.service import (
    LeavePolicyService,
    employee_join,
    fmt_days,
    request_days,
    working_dates,
)
from src.modules.hr_notifications.service import (
    LEAVE_APPROVED,
    LEAVE_REJECTED,
    LEAVE_SUBMITTED,
    HrNotifier,
)
from src.modules.hr_settings.service import load_rules
from src.shared.utils import utcnow

# Legacy slips are visible once PAID. Production payroll publishes first (PUBLISHED)
# and marks PAID when the bank payment is confirmed. Employees see both.
EMPLOYEE_VISIBLE_PAYSLIP_STATUSES = ("PAID", "PUBLISHED")
EMPLOYEE_VISIBLE_PAYSLIP = "PAID"


def _money(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


def _fmt_day(d: date) -> str:
    return d.strftime("%d %b %Y")


def _leave_span(row: StaffLeaveRequest) -> str:
    dates = (
        _fmt_day(row.start_date)
        if row.start_date == row.end_date
        else f"{_fmt_day(row.start_date)} – {_fmt_day(row.end_date)}"
    )
    if row.working_days is not None:
        n = request_days(row)
        return f"{dates} ({fmt_days(n)} working day{'s' if n != 1 else ''})"
    days = (row.end_date - row.start_date).days + 1
    return f"{dates} ({days} day{'s' if days != 1 else ''})"


class HRService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Leave (staff) ─────────────────────────────────────────

    async def create_staff_leave(
        self,
        school_id: UUID,
        user_id: UUID,
        data: StaffLeaveRequestCreate,
        *,
        resubmitted_from_id: UUID | None = None,
    ) -> StaffLeaveRequestOut:
        if data.end_date < data.start_date:
            raise AppError("End date must be on or after the start date", status_code=422)
        rules = await load_rules(self.db, school_id)
        max_days = rules.leave_max_days
        if (data.end_date - data.start_date).days >= max_days:
            raise AppError(
                f"A leave request cannot exceed {max_days} day{'s' if max_days != 1 else ''}",
                status_code=422,
            )
        notice = rules.leave_min_notice_days
        if notice and data.start_date < date.today() + timedelta(days=notice):
            raise AppError(
                f"Leave must be requested at least {notice} day{'s' if notice != 1 else ''} in advance",
                status_code=422,
            )
        await employee_for_user(self.db, school_id, user_id)
        policy = LeavePolicyService(self.db)
        leave_type: HrLeaveType | None = None
        if data.leave_type_id:
            leave_type = await policy.get_type(school_id, data.leave_type_id)
            if not leave_type.is_active:
                raise AppError(f"{leave_type.name} is no longer offered", status_code=422)
        elif await policy.has_active_types(school_id):
            raise AppError("Choose a leave type", status_code=422)
        days = Decimal(len(await working_dates(self.db, school_id, data.start_date, data.end_date)))
        if not days:
            raise AppError(
                "These dates fall entirely on non-working days or school holidays", status_code=422
            )
        if leave_type and leave_type.max_consecutive_days and days > leave_type.max_consecutive_days:
            raise AppError(
                f"{leave_type.name} allows at most {leave_type.max_consecutive_days} working days per request",
                status_code=422,
            )
        overlap = (
            await self.db.execute(
                select(StaffLeaveRequest)
                .where(
                    StaffLeaveRequest.school_id == school_id,
                    StaffLeaveRequest.user_id == user_id,
                    StaffLeaveRequest.status.in_(("PENDING", "APPROVED")),
                    StaffLeaveRequest.start_date <= data.end_date,
                    StaffLeaveRequest.end_date >= data.start_date,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if overlap:
            raise ConflictError(
                f"These dates overlap your {overlap.status.lower()} leave request for "
                f"{_fmt_day(overlap.start_date)} – {_fmt_day(overlap.end_date)}"
            )
        if leave_type:
            await policy.assert_within_balance(
                school_id, user_id, leave_type, days, data.start_date.year
            )
        row = StaffLeaveRequest(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            start_date=data.start_date,
            end_date=data.end_date,
            reason=data.reason.strip(),
            status="PENDING",
            leave_type_id=leave_type.id if leave_type else None,
            working_days=days,
            resubmitted_from_id=resubmitted_from_id,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(row)
                await self.db.flush()
        except IntegrityError as exc:
            if resubmitted_from_id:
                raise ConflictError("This request has already been resubmitted") from exc
            raise
        out = await self._leave_out(school_id, row.id)
        await HrNotifier(self.db).to_admins(
            school_id,
            kind="leave",
            category=LEAVE_SUBMITTED,
            title=f"{'Resubmitted leave' if resubmitted_from_id else 'Leave'} request from {out.staff_name or 'a staff member'}",
            body=f"{out.leave_type_name + ' · ' if out.leave_type_name else ''}{_leave_span(row)} · {row.reason}",
            entity_id=row.id,
            event="submitted",
            actor=user_id,
        )
        return out

    async def resubmit_staff_leave(
        self, school_id: UUID, leave_id: UUID, user_id: UUID, data: StaffLeaveRequestCreate
    ) -> StaffLeaveRequestOut:
        """A rejected or withdrawn request may be resubmitted once, as a new linked request."""
        row = await self.db.get(StaffLeaveRequest, leave_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Leave request not found")
        if row.user_id != user_id:
            raise ForbiddenError("Cannot resubmit another staff member's leave request")
        if row.status not in ("REJECTED", "CANCELLED"):
            raise AppError("Only rejected or withdrawn requests can be resubmitted", status_code=422)
        already = await self.db.scalar(
            select(StaffLeaveRequest.id).where(StaffLeaveRequest.resubmitted_from_id == row.id)
        )
        if already:
            raise ConflictError("This request has already been resubmitted")
        return await self.create_staff_leave(school_id, user_id, data, resubmitted_from_id=row.id)

    async def list_staff_leaves(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        user_id: UUID | None = None,
        q: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        leave_type_id: UUID | None = None,
        limit: int = 500,
    ) -> list[StaffLeaveRequestOut]:
        if date_from and date_to and date_from > date_to:
            raise AppError("date_from must be on or before date_to", status_code=422)
        stmt = self._leave_query(school_id)
        if status:
            stmt = stmt.where(StaffLeaveRequest.status == status.upper().strip())
        if user_id:
            stmt = stmt.where(StaffLeaveRequest.user_id == user_id)
        if leave_type_id:
            stmt = stmt.where(StaffLeaveRequest.leave_type_id == leave_type_id)
        if date_from:
            stmt = stmt.where(StaffLeaveRequest.end_date >= date_from)
        if date_to:
            stmt = stmt.where(StaffLeaveRequest.start_date <= date_to)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(User.full_name.ilike(like), employee_code_expr().ilike(like), StaffLeaveRequest.reason.ilike(like))
            )
        rows = (await self.db.execute(stmt.order_by(StaffLeaveRequest.created_at.desc()).limit(limit))).all()
        return [self._to_leave_out(*r) for r in rows]

    async def cancel_staff_leave(self, school_id: UUID, leave_id: UUID, user_id: UUID) -> StaffLeaveRequestOut:
        row = await self.db.get(StaffLeaveRequest, leave_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Leave request not found")
        if row.user_id != user_id:
            raise ForbiddenError("Cannot cancel another staff member's leave request")
        if row.status not in ("PENDING",):
            raise AppError("Only PENDING requests can be cancelled", status_code=422)
        row.status = "CANCELLED"
        await self.db.flush()
        out = await self._leave_out(school_id, row.id)
        await HrNotifier(self.db).to_admins(
            school_id,
            kind="leave",
            category=LEAVE_SUBMITTED,
            title=f"Leave request withdrawn by {out.staff_name or 'a staff member'}",
            body=_leave_span(row),
            entity_id=row.id,
            event="withdrawn",
            actor=user_id,
        )
        return out

    async def revoke_staff_leave(
        self, school_id: UUID, leave_id: UUID, actor: UUID, data: StaffLeaveRevokeIn
    ) -> StaffLeaveRequestOut:
        """HR cancels an approved leave; the LEAVE marks it created are removed from attendance."""
        row = await self.db.get(StaffLeaveRequest, leave_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Leave request not found")
        if row.status != "APPROVED":
            raise AppError("Only approved leave can be cancelled by HR", status_code=422)
        row.status = "CANCELLED"
        row.decision_note = "\n".join(
            x for x in (row.decision_note, f"Cancelled by HR: {data.reason}") if x
        )
        marks = list(
            (
                await self.db.execute(
                    select(StaffAttendance).where(
                        StaffAttendance.school_id == school_id,
                        StaffAttendance.leave_request_id == row.id,
                        StaffAttendance.status == "LEAVE",
                    )
                )
            ).scalars()
        )
        removed = sorted(m.attendance_date for m in marks)
        for m in marks:
            await self.db.delete(m)
        await self.db.flush()
        await HrNotifier(self.db).to_employee(
            school_id,
            row.user_id,
            kind="leave",
            category=LEAVE_REJECTED,
            title="Your approved leave was cancelled by HR",
            body=f"{_leave_span(row)} · {data.reason}",
            entity_id=row.id,
            event="revoked",
            actor=actor,
        )
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=actor,
            action="hr.leave.revoked",
            resource_type="staff_leave_request",
            resource_id=row.id,
            message=f"Approved leave {_leave_span(row)} cancelled",
            details={"reason": data.reason, "attendance_marks_removed": removed},
        )
        return await self._leave_out(school_id, row.id)

    async def decide_staff_leave(
        self, school_id: UUID, leave_id: UUID, decided_by: UUID, data: StaffLeaveDecisionIn
    ) -> StaffLeaveRequestOut:
        row = await self.db.get(StaffLeaveRequest, leave_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Leave request not found")
        if row.status != "PENDING":
            raise AppError("Only PENDING requests can be decided", status_code=422)
        if row.user_id == decided_by:
            raise ForbiddenError("You cannot decide your own leave request")
        decision = data.decision.upper().strip()
        if decision == "APPROVED" and row.leave_type_id:
            policy = LeavePolicyService(self.db)
            await policy.assert_within_balance(
                school_id,
                row.user_id,
                await policy.get_type(school_id, row.leave_type_id),
                request_days(row),
                row.start_date.year,
                exclude_leave_id=row.id,
            )
        row.status = decision
        row.decided_by = decided_by
        row.decided_at = utcnow()
        row.decision_note = data.decision_note
        await self.db.flush()
        approved = row.status == "APPROVED"
        marked, overwritten = (await self._apply_leave_to_attendance(row, decided_by)) if approved else ([], [])
        await HrNotifier(self.db).to_employee(
            school_id,
            row.user_id,
            kind="leave",
            category=LEAVE_APPROVED if approved else LEAVE_REJECTED,
            title=f"Your leave request was {'approved' if approved else 'rejected'}",
            body=_leave_span(row) + (f" · {row.decision_note}" if row.decision_note else ""),
            entity_id=row.id,
            event=decision.lower(),
            actor=decided_by,
        )
        if approved and row.user_id:
            from src.db.models import User
            from src.modules.alerts.service import dispatch_alert, school_display_name

            person = await self.db.get(User, row.user_id)
            try:
                await dispatch_alert(
                    self.db,
                    school_id=school_id,
                    event_key="leave_approved",
                    notification_type=LEAVE_APPROVED,
                    staff_user_ids=[row.user_id],
                    variables={
                        "employee_name": person.full_name if person else "Employee",
                        "leave_type": "Paid leave" if row.is_paid else "Unpaid leave",
                        "from_date": row.start_date.strftime("%d %b %Y"),
                        "to_date": row.end_date.strftime("%d %b %Y"),
                        "school_name": await school_display_name(self.db, school_id),
                    },
                    dedupe_key=f"leave_approved:{row.id}",
                    title="Leave approved",
                    link_path="/hrm/leave",
                )
            except Exception:
                from src.core.telemetry import track_alert

                track_alert("hr_alert_failed", leave_id=str(row.id))
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=decided_by,
            action=f"hr.leave.{decision.lower()}",
            resource_type="staff_leave_request",
            resource_id=row.id,
            message=f"Leave {_leave_span(row)} {decision.lower()}",
            details={
                "user_id": row.user_id,
                "note": row.decision_note,
                "attendance_marked_leave": marked,
                "attendance_overwritten": overwritten,
            },
        )
        return await self._leave_out(school_id, row.id)

    async def _apply_leave_to_attendance(
        self, row: StaffLeaveRequest, actor: UUID
    ) -> tuple[list[date], list[dict]]:
        """Mark every working day of an approved leave as LEAVE for the employee.

        Any earlier mark on those days is replaced (the replaced status is returned for the audit
        trail); marks written here carry ``leave_request_id`` so cancelling the leave removes them.
        """
        try:
            emp = await employee_for_user(self.db, row.school_id, row.user_id)
        except ForbiddenError:
            return [], []
        days = await working_dates(self.db, row.school_id, row.start_date, row.end_date)
        if not days:
            return [], []
        existing = {
            rec.attendance_date: rec
            for rec in (
                await self.db.execute(
                    select(StaffAttendance).where(
                        StaffAttendance.employee_id == emp.id,
                        StaffAttendance.attendance_date.in_(days),
                    )
                )
            ).scalars()
        }
        overwritten = []
        for d in days:
            rec = existing.get(d)
            if rec is None:
                self.db.add(
                    StaffAttendance(
                        school_id=row.school_id,
                        employee_id=emp.id,
                        attendance_date=d,
                        status="LEAVE",
                        notes="On approved leave",
                        marked_by=actor,
                        leave_request_id=row.id,
                    )
                )
                continue
            if rec.status != "LEAVE":
                overwritten.append({"date": d, "status": rec.status})
            rec.status = "LEAVE"
            rec.check_in = rec.check_out = None
            rec.leave_request_id = row.id
            rec.marked_by = actor
        await self.db.flush()
        return days, overwritten

    @staticmethod
    def _leave_query(school_id: UUID):
        decider = aliased(User)
        resubmission = aliased(StaffLeaveRequest)
        return (
            select(
                StaffLeaveRequest,
                User.full_name,
                employee_code_expr(),
                Department.name,
                decider.full_name,
                HrLeaveType.name,
                HrLeaveType.is_paid,
                resubmission.id,
            )
            .outerjoin(User, User.id == StaffLeaveRequest.user_id)
            .outerjoin(
                Teacher,
                and_(Teacher.user_id == StaffLeaveRequest.user_id, Teacher.school_id == StaffLeaveRequest.school_id),
            )
            .outerjoin(Employee, employee_join(StaffLeaveRequest))
            .outerjoin(Department, Department.id == Employee.department_id)
            .outerjoin(decider, decider.id == StaffLeaveRequest.decided_by)
            .outerjoin(HrLeaveType, HrLeaveType.id == StaffLeaveRequest.leave_type_id)
            .outerjoin(resubmission, resubmission.resubmitted_from_id == StaffLeaveRequest.id)
            .where(StaffLeaveRequest.school_id == school_id)
            # updated_at is set server-side (onupdate) and expired by flush; reload it with the row,
            # since lazy-loading it during sync serialization raises MissingGreenlet.
            .execution_options(populate_existing=True)
        )

    async def _leave_out(self, school_id: UUID, leave_id: UUID) -> StaffLeaveRequestOut:
        row = (await self.db.execute(self._leave_query(school_id).where(StaffLeaveRequest.id == leave_id))).one()
        return self._to_leave_out(*row)

    @staticmethod
    def _to_leave_out(
        leave: StaffLeaveRequest,
        staff_name: str | None,
        employee_code: str | None,
        department_name: str | None,
        decided_by_name: str | None,
        leave_type_name: str | None = None,
        leave_type_is_paid: bool | None = None,
        resubmitted_as_id: UUID | None = None,
    ) -> StaffLeaveRequestOut:
        out = StaffLeaveRequestOut.model_validate(leave)
        out.staff_name = staff_name
        out.employee_code = employee_code
        out.department_name = department_name
        out.decided_by_name = decided_by_name
        out.days = (leave.end_date - leave.start_date).days + 1
        out.leave_type_name = leave_type_name
        out.leave_type_is_paid = leave_type_is_paid
        out.resubmitted_as_id = resubmitted_as_id
        return out

    # ── Payroll ───────────────────────────────────────────────

    async def create_payroll_run(self, school_id: UUID, created_by: UUID, data: PayrollRunCreate) -> PayrollRunOut:
        if data.period_end < data.period_start:
            raise AppError("period_end must be >= period_start", status_code=422)
        row = PayrollRun(
            id=uuid4(),
            school_id=school_id,
            label=data.label.strip(),
            period_start=data.period_start,
            period_end=data.period_end,
            status="DRAFT",
            created_by=created_by,
        )
        self.db.add(row)
        await self.db.flush()
        return PayrollRunOut.model_validate(row)

    async def list_payroll_runs(self, school_id: UUID, limit: int = 50) -> list[PayrollRunOut]:
        rows = (
            await self.db.execute(
                select(
                    PayrollRun,
                    func.count(PayrollPayslip.id).filter(PayrollPayslip.status != "CANCELLED"),
                    func.count(PayrollPayslip.id).filter(PayrollPayslip.status.in_(EMPLOYEE_VISIBLE_PAYSLIP_STATUSES)),
                )
                .outerjoin(
                    PayrollPayslip,
                    and_(
                        PayrollPayslip.payroll_run_id == PayrollRun.id,
                        PayrollPayslip.school_id == PayrollRun.school_id,
                    ),
                )
                .where(PayrollRun.school_id == school_id)
                .group_by(PayrollRun.id)
                .order_by(PayrollRun.period_start.desc(), PayrollRun.created_at.desc())
                .limit(limit)
            )
        ).all()
        out: list[PayrollRunOut] = []
        for run, total, paid in rows:
            item = PayrollRunOut.model_validate(run)
            item.payslip_count, item.paid_count = total, paid
            out.append(item)
        return out

    async def get_payroll_run(self, school_id: UUID, run_id: UUID) -> PayrollRunDetailOut:
        run = await self.db.get(PayrollRun, run_id)
        if not run or run.school_id != school_id:
            raise NotFoundError("Payroll run not found")
        rows = (
            await self.db.execute(
                self._payslip_query(school_id)
                .where(PayrollPayslip.payroll_run_id == run_id)
                .order_by(func.lower(User.full_name), PayrollPayslip.created_at.asc())
            )
        ).all()
        return PayrollRunDetailOut(
            run=PayrollRunOut.model_validate(run),
            payslips=[self._to_payslip_out(*r) for r in rows],
        )

    def _payslip_query(self, school_id: UUID):
        """Payslips of one school with the employee and payroll period they belong to."""
        return (
            select(
                PayrollPayslip,
                User.full_name,
                func.coalesce(Teacher.employee_code, Teacher.teacher_code, Employee.employee_code),
                Department.name,
                PayrollRun,
            )
            .join(
                PayrollRun,
                and_(
                    PayrollRun.id == PayrollPayslip.payroll_run_id,
                    PayrollRun.school_id == PayrollPayslip.school_id,
                ),
            )
            .outerjoin(Teacher, Teacher.id == PayrollPayslip.teacher_id)
            .outerjoin(
                Employee,
                or_(
                    and_(PayrollPayslip.employee_id.is_not(None), Employee.id == PayrollPayslip.employee_id),
                    and_(
                        PayrollPayslip.employee_id.is_(None),
                        Employee.teacher_id == PayrollPayslip.teacher_id,
                    ),
                ),
            )
            .outerjoin(User, User.id == func.coalesce(Employee.user_id, Teacher.user_id))
            .outerjoin(Department, Department.id == Employee.department_id)
            .where(PayrollPayslip.school_id == school_id)
        )

    @staticmethod
    def _to_payslip_out(
        slip: PayrollPayslip,
        name: str | None,
        code: str | None,
        department: str | None,
        run: PayrollRun,
    ) -> PayrollPayslipOut:
        out = PayrollPayslipOut.model_validate(slip)
        out.employee_name = name
        out.employee_code = code
        out.department_name = department
        out.run_label = run.label
        out.period_start = run.period_start
        out.period_end = run.period_end
        return out

    async def list_payslips(
        self,
        school_id: UUID,
        *,
        run_id: UUID | None = None,
        status: str | None = None,
        teacher_id: UUID | None = None,
        year: int | None = None,
        q: str | None = None,
        limit: int = 500,
    ) -> list[PayrollPayslipOut]:
        stmt = self._payslip_query(school_id)
        if run_id:
            stmt = stmt.where(PayrollPayslip.payroll_run_id == run_id)
        if status:
            stmt = stmt.where(PayrollPayslip.status == status.upper())
        if teacher_id:
            stmt = stmt.where(PayrollPayslip.teacher_id == teacher_id)
        if year:
            stmt = stmt.where(func.extract("year", PayrollRun.period_start) == year)
        if q and q.strip():
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.full_name).like(like),
                    func.lower(func.coalesce(Teacher.employee_code, Teacher.teacher_code, "")).like(like),
                )
            )
        rows = (
            await self.db.execute(
                stmt.order_by(
                    PayrollRun.period_start.desc(), func.lower(User.full_name), PayrollPayslip.created_at.asc()
                ).limit(limit)
            )
        ).all()
        return [self._to_payslip_out(*r) for r in rows]

    async def create_payslip(
        self, school_id: UUID, run_id: UUID, data: PayrollPayslipCreate
    ) -> PayrollPayslipOut:
        run = await self.db.get(PayrollRun, run_id)
        if not run or run.school_id != school_id:
            raise NotFoundError("Payroll run not found")
        teacher = await self.db.get(Teacher, data.teacher_id)
        if not teacher or teacher.school_id != school_id:
            raise NotFoundError("Employee not found")
        employee = (
            await self.db.execute(
                select(Employee).where(
                    Employee.school_id == school_id,
                    Employee.teacher_id == teacher.id,
                )
            )
        ).scalar_one_or_none()
        gross = _money(data.gross_amount)
        ded = _money(data.deductions_amount or Decimal("0.00"))
        net = _money(gross - ded)
        if net < 0:
            raise AppError("Deductions cannot exceed the gross amount", status_code=422)
        await self._assert_no_active_payslip(run_id, data.teacher_id)
        row = PayrollPayslip(
            id=uuid4(),
            school_id=school_id,
            payroll_run_id=run_id,
            teacher_id=data.teacher_id,
            employee_id=employee.id if employee else None,
            gross_amount=gross,
            deductions_amount=ded,
            net_amount=net,
            status="DRAFT",
            notes=data.notes,
        )
        self.db.add(row)
        await self._flush_payslip()
        return PayrollPayslipOut.model_validate(row)

    async def update_payslip(
        self, school_id: UUID, payslip_id: UUID, data: PayrollPayslipUpdate
    ) -> PayrollPayslipOut:
        row = await self.db.get(PayrollPayslip, payslip_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Payslip not found")
        gross = _money(data.gross_amount) if data.gross_amount is not None else _money(row.gross_amount)
        ded = _money(data.deductions_amount) if data.deductions_amount is not None else _money(row.deductions_amount)
        net = _money(data.net_amount) if data.net_amount is not None else _money(gross - ded)
        if net < 0:
            raise AppError("Deductions cannot exceed the gross amount", status_code=422)
        status = data.status or row.status
        if row.status == "CANCELLED" and status != "CANCELLED":
            await self._assert_no_active_payslip(row.payroll_run_id, row.teacher_id, exclude_id=row.id)
        linked = None
        if row.employee_id is None and row.teacher_id is not None:
            linked = (
                await self.db.execute(
                    select(Employee.id).where(
                        Employee.school_id == school_id,
                        Employee.teacher_id == row.teacher_id,
                    )
                )
            ).scalar_one_or_none()

        row.gross_amount, row.deductions_amount, row.net_amount = gross, ded, net
        row.status = status
        if linked is not None:
            row.employee_id = linked
        if data.paid_on is not None:
            row.paid_on = data.paid_on
        elif status == "PAID" and row.paid_on is None:
            row.paid_on = date.today()
        if data.notes is not None:
            row.notes = data.notes
        await self._flush_payslip()
        await self.db.refresh(row)
        return PayrollPayslipOut.model_validate(row)

    async def _assert_no_active_payslip(
        self, run_id: UUID, teacher_id: UUID, exclude_id: UUID | None = None
    ) -> None:
        q = select(PayrollPayslip.id).where(
            PayrollPayslip.payroll_run_id == run_id,
            PayrollPayslip.teacher_id == teacher_id,
            PayrollPayslip.status != "CANCELLED",
        )
        if exclude_id:
            q = q.where(PayrollPayslip.id != exclude_id)
        if (await self.db.execute(q.limit(1))).scalar_one_or_none():
            raise ConflictError("This employee already has a payslip in this payroll run")

    async def _flush_payslip(self) -> None:
        try:
            async with self.db.begin_nested():
                await self.db.flush()
        except IntegrityError as exc:
            if "uq_payroll_payslips_run_teacher_active" in str(exc.orig):
                raise ConflictError("This employee already has a payslip in this payroll run") from exc
            raise

    async def list_my_payslips(
        self, school_id: UUID, user_id: UUID, *, year: int | None = None
    ) -> list[PayrollPayslipOut]:
        try:
            brief = await employee_for_user(self.db, school_id, user_id)
        except ForbiddenError:
            return []
        emp = await self.db.get(Employee, brief.id)
        if emp is None:
            return []
        owned = [PayrollPayslip.employee_id == emp.id]
        if emp.teacher_id is not None:
            owned.append(PayrollPayslip.teacher_id == emp.teacher_id)
        stmt = self._payslip_query(school_id).where(
            or_(*owned),
            PayrollPayslip.status.in_(EMPLOYEE_VISIBLE_PAYSLIP_STATUSES),
        )
        if year:
            stmt = stmt.where(func.extract("year", PayrollRun.period_start) == year)
        rows = (
            await self.db.execute(
                stmt.order_by(PayrollRun.period_start.desc(), PayrollPayslip.created_at.desc()).limit(100)
            )
        ).all()
        return [self._to_payslip_out(*r) for r in rows]

    async def build_payslip_pdf(
        self, school_id: UUID, payslip_id: UUID, *, viewer_user_id: UUID, viewer_is_admin: bool
    ) -> tuple[bytes, str]:
        row = (
            await self.db.execute(self._payslip_query(school_id).where(PayrollPayslip.id == payslip_id))
        ).first()
        if row is None:
            raise NotFoundError("Payslip not found")
        slip, name, code, _dept, run = row
        if not viewer_is_admin:
            owns = False
            try:
                brief = await employee_for_user(self.db, school_id, viewer_user_id)
            except ForbiddenError:
                brief = None
            emp = await self.db.get(Employee, brief.id) if brief is not None else None
            if emp is not None and (
                slip.employee_id == emp.id or (emp.teacher_id is not None and slip.teacher_id == emp.teacher_id)
            ):
                owns = True
            if not owns and slip.teacher_id is not None:
                teacher = await self.db.get(Teacher, slip.teacher_id)
                owns = teacher is not None and teacher.user_id == viewer_user_id
            if not owns:
                raise ForbiddenError("Not allowed")
            if slip.status not in EMPLOYEE_VISIBLE_PAYSLIP_STATUSES:
                raise NotFoundError("Payslip not found")

        school = await self.db.get(School, school_id)
        payload = {
            "school_name": school.name if school else "School",
            "run_label": run.label,
            "period": f"{_fmt_day(run.period_start)} to {_fmt_day(run.period_end)}",
            "teacher_name": name or "Employee",
            "employee_code": code,
            "gross_amount": str(slip.gross_amount),
            "deductions_amount": str(slip.deductions_amount),
            "net_amount": str(slip.net_amount),
            "status": slip.status,
            "paid_on": _fmt_day(slip.paid_on) if slip.paid_on else None,
            "notes": slip.notes,
        }
        safe_code = re.sub(r"[^A-Za-z0-9_-]+", "", code or "") or "employee"
        filename = f"payslip-{safe_code}-{run.period_start:%Y-%m}.pdf"
        return build_payslip_pdf(payload), filename

