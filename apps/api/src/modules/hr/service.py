from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ForbiddenError, NotFoundError
from src.db.models import PayrollPayslip, PayrollRun, School, StaffLeaveRequest, Teacher, User
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


def _money(x: Decimal) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))


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
            raise AppError("end_date must be >= start_date", status_code=422)
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
        return StaffLeaveRequestOut.model_validate(row)

    async def list_staff_leaves(
        self,
        school_id: UUID,
        *,
        status: str | None = None,
        user_id: UUID | None = None,
        limit: int = 200,
    ) -> list[StaffLeaveRequestOut]:
        q = select(StaffLeaveRequest).where(StaffLeaveRequest.school_id == school_id)
        if status:
            q = q.where(StaffLeaveRequest.status == status.upper().strip())
        if user_id:
            q = q.where(StaffLeaveRequest.user_id == user_id)
        rows = (await self.db.execute(q.order_by(StaffLeaveRequest.created_at.desc()).limit(limit))).scalars().all()
        return [StaffLeaveRequestOut.model_validate(r) for r in rows]

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
        return StaffLeaveRequestOut.model_validate(row)

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
        return StaffLeaveRequestOut.model_validate(row)

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
        gross = _money(data.gross_amount)
        ded = _money(data.deductions_amount or Decimal("0.00"))
        net = _money(gross - ded)
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
        await self.db.flush()
        return PayrollPayslipOut.model_validate(row)

    async def update_payslip(
        self, school_id: UUID, payslip_id: UUID, data: PayrollPayslipUpdate
    ) -> PayrollPayslipOut:
        row = await self.db.get(PayrollPayslip, payslip_id)
        if not row or row.school_id != school_id:
            raise NotFoundError("Payslip not found")
        if data.gross_amount is not None:
            row.gross_amount = _money(data.gross_amount)
        if data.deductions_amount is not None:
            row.deductions_amount = _money(data.deductions_amount)
        if data.net_amount is not None:
            row.net_amount = _money(data.net_amount)
        else:
            row.net_amount = _money(Decimal(str(row.gross_amount)) - Decimal(str(row.deductions_amount)))
        if data.status is not None:
            row.status = data.status.upper().strip()
        if data.paid_on is not None:
            row.paid_on = data.paid_on
        if data.notes is not None:
            row.notes = data.notes
        await self.db.flush()
        return PayrollPayslipOut.model_validate(row)

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

