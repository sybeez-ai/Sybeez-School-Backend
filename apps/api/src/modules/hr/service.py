from __future__ import annotations

from datetime import date
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
    PayrollPayslip,
    PayrollRun,
    School,
    StaffLeaveRequest,
    Teacher,
    User,
)
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
)
from src.shared.utils import utcnow

MAX_LEAVE_DAYS = 366


def _money(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


def _fmt_day(d: date) -> str:
    return d.strftime("%d %b %Y")


def _teacher_code():
    return func.coalesce(Teacher.employee_code, Teacher.teacher_code)


class HRService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _teacher_for_user(self, school_id: UUID, user_id: UUID) -> Teacher:
        t = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not t:
            raise ForbiddenError("Teacher profile required")
        return t

    # ── Leave (staff) ─────────────────────────────────────────

    async def create_staff_leave(self, school_id: UUID, user_id: UUID, data: StaffLeaveRequestCreate) -> StaffLeaveRequestOut:
        if data.end_date < data.start_date:
            raise AppError("End date must be on or after the start date", status_code=422)
        if (data.end_date - data.start_date).days >= MAX_LEAVE_DAYS:
            raise AppError(f"A leave request cannot exceed {MAX_LEAVE_DAYS} days", status_code=422)
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
        row = StaffLeaveRequest(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            start_date=data.start_date,
            end_date=data.end_date,
            reason=data.reason.strip(),
            status="PENDING",
        )
        self.db.add(row)
        await self.db.flush()
        return await self._leave_out(school_id, row.id)

    async def list_staff_leaves(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        user_id: UUID | None = None,
        q: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 500,
    ) -> list[StaffLeaveRequestOut]:
        if date_from and date_to and date_from > date_to:
            raise AppError("date_from must be on or before date_to", status_code=422)
        stmt = self._leave_query(school_id)
        if status:
            stmt = stmt.where(StaffLeaveRequest.status == status.upper().strip())
        if user_id:
            stmt = stmt.where(StaffLeaveRequest.user_id == user_id)
        if date_from:
            stmt = stmt.where(StaffLeaveRequest.end_date >= date_from)
        if date_to:
            stmt = stmt.where(StaffLeaveRequest.start_date <= date_to)
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(
                or_(User.full_name.ilike(like), _teacher_code().ilike(like), StaffLeaveRequest.reason.ilike(like))
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
        return await self._leave_out(school_id, row.id)

    async def decide_staff_leave(
        self, school_id: UUID, leave_id: UUID, decided_by: UUID, data: StaffLeaveDecisionIn
    ) -> StaffLeaveRequestOut:
        row = await self.db.get(StaffLeaveRequest, leave_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Leave request not found")
        if row.status != "PENDING":
            raise AppError("Only PENDING requests can be decided", status_code=422)
        row.status = data.decision.upper().strip()
        row.decided_by = decided_by
        row.decided_at = utcnow()
        row.decision_note = data.decision_note
        await self.db.flush()
        return await self._leave_out(school_id, row.id)

    @staticmethod
    def _leave_query(school_id: UUID):
        decider = aliased(User)
        return (
            select(StaffLeaveRequest, User.full_name, _teacher_code(), Department.name, decider.full_name)
            .outerjoin(User, User.id == StaffLeaveRequest.user_id)
            .outerjoin(
                Teacher,
                and_(Teacher.user_id == StaffLeaveRequest.user_id, Teacher.school_id == StaffLeaveRequest.school_id),
            )
            .outerjoin(Employee, Employee.teacher_id == Teacher.id)
            .outerjoin(Department, Department.id == Employee.department_id)
            .outerjoin(decider, decider.id == StaffLeaveRequest.decided_by)
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
    ) -> StaffLeaveRequestOut:
        out = StaffLeaveRequestOut.model_validate(leave)
        out.staff_name = staff_name
        out.employee_code = employee_code
        out.department_name = department_name
        out.decided_by_name = decided_by_name
        out.days = (leave.end_date - leave.start_date).days + 1
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
                select(PayrollRun).where(PayrollRun.school_id == school_id).order_by(PayrollRun.created_at.desc()).limit(limit)
            )
        ).scalars().all()
        return [PayrollRunOut.model_validate(r) for r in rows]

    async def get_payroll_run(self, school_id: UUID, run_id: UUID) -> PayrollRunDetailOut:
        run = await self.db.get(PayrollRun, run_id)
        if not run or run.school_id != school_id:
            raise NotFoundError("Payroll run not found")
        slips = (
            await self.db.execute(
                select(PayrollPayslip).where(
                    PayrollPayslip.school_id == school_id, PayrollPayslip.payroll_run_id == run_id
                ).order_by(PayrollPayslip.created_at.asc())
            )
        ).scalars().all()
        return PayrollRunDetailOut(
            run=PayrollRunOut.model_validate(run),
            payslips=[PayrollPayslipOut.model_validate(s) for s in slips],
        )

    async def create_payslip(
        self, school_id: UUID, run_id: UUID, data: PayrollPayslipCreate
    ) -> PayrollPayslipOut:
        run = await self.db.get(PayrollRun, run_id)
        if not run or run.school_id != school_id:
            raise NotFoundError("Payroll run not found")
        teacher = await self.db.get(Teacher, data.teacher_id)
        if not teacher or teacher.school_id != school_id:
            raise NotFoundError("Employee not found")
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

        row.gross_amount, row.deductions_amount, row.net_amount = gross, ded, net
        row.status = status
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

    async def list_my_payslips(self, school_id: UUID, user_id: UUID) -> list[PayrollPayslipOut]:
        teacher = await self._teacher_for_user(school_id, user_id)
        rows = (
            await self.db.execute(
                select(PayrollPayslip)
                .where(PayrollPayslip.school_id == school_id, PayrollPayslip.teacher_id == teacher.id)
                .order_by(PayrollPayslip.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        return [PayrollPayslipOut.model_validate(r) for r in rows]

    async def build_payslip_pdf(self, school_id: UUID, payslip_id: UUID, *, viewer_user_id: UUID, viewer_is_admin: bool) -> bytes:
        row = await self.db.get(PayrollPayslip, payslip_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Payslip not found")
        teacher = await self.db.get(Teacher, row.teacher_id)
        if not teacher:
            raise NotFoundError("Teacher not found")
        if not viewer_is_admin:
            if teacher.user_id != viewer_user_id:
                raise ForbiddenError("Not allowed")

        run = await self.db.get(PayrollRun, row.payroll_run_id)
        school = await self.db.get(School, school_id)
        u = await self.db.get(User, teacher.user_id)
        teacher_name = u.full_name if u else "Teacher"

        period = f"{run.period_start} to {run.period_end}" if run else "-"
        payload = {
            "school_name": school.name if school else "School",
            "run_label": run.label if run else "-",
            "period": period,
            "teacher_name": teacher_name,
            "employee_code": teacher.employee_code,
            "gross_amount": str(row.gross_amount),
            "deductions_amount": str(row.deductions_amount),
            "net_amount": str(row.net_amount),
            "status": row.status,
            "paid_on": str(row.paid_on) if row.paid_on else None,
            "notes": row.notes,
        }
        return build_payslip_pdf(payload)

