from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
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
from src.modules.hr.service import HRService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr"])

LEAVE_STATUS_PATTERN = "(?i)^(PENDING|APPROVED|REJECTED|CANCELLED)$"
PAYSLIP_STATUS_PATTERN = "(?i)^(DRAFT|PUBLISHED|PAID|CANCELLED)$"


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
async def hr_health():
    return {"module": "hr", "status": "ok"}


# ── Staff leave ──────────────────────────────────────────────


@router.get("/leave/requests", response_model=list[StaffLeaveRequestOut])
async def list_staff_leaves(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
    status: str | None = Query(default=None, pattern=LEAVE_STATUS_PATTERN),
    q: str | None = Query(default=None, max_length=200),
    date_from: date | None = None,
    date_to: date | None = None,
    user_id: UUID | None = None,
):
    return await HRService(db).list_staff_leaves(
        _sid(user), status=status, q=q, date_from=date_from, date_to=date_to, user_id=user_id
    )


@router.get("/leave/requests/me", response_model=list[StaffLeaveRequestOut])
async def list_my_staff_leaves(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await HRService(db).list_staff_leaves(_sid(user), user_id=user.id)


@router.post("/leave/requests", response_model=StaffLeaveRequestOut, status_code=201)
async def create_my_staff_leave(
    body: StaffLeaveRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await HRService(db).create_staff_leave(_sid(user), user.id, body)


@router.post("/leave/requests/{leave_id}/cancel", response_model=StaffLeaveRequestOut)
async def cancel_my_staff_leave(
    leave_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await HRService(db).cancel_staff_leave(_sid(user), leave_id, user.id)


@router.post("/leave/requests/{leave_id}/decide", response_model=StaffLeaveRequestOut)
async def decide_staff_leave(
    leave_id: UUID,
    body: StaffLeaveDecisionIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).decide_staff_leave(_sid(user), leave_id, user.id, body)


# ── Payroll ──────────────────────────────────────────────────


@router.get("/payroll/runs", response_model=list[PayrollRunOut])
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).list_payroll_runs(_sid(user))


@router.post("/payroll/runs", response_model=PayrollRunOut, status_code=201)
async def create_run(
    body: PayrollRunCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).create_payroll_run(_sid(user), user.id, body)


@router.get("/payroll/runs/{run_id}", response_model=PayrollRunDetailOut)
async def get_run(
    run_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).get_payroll_run(_sid(user), run_id)


@router.post("/payroll/runs/{run_id}/payslips", response_model=PayrollPayslipOut, status_code=201)
async def create_run_payslip(
    run_id: UUID,
    body: PayrollPayslipCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).create_payslip(_sid(user), run_id, body)


@router.patch("/payroll/payslips/{payslip_id}", response_model=PayrollPayslipOut)
async def update_payslip(
    payslip_id: UUID,
    body: PayrollPayslipUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
):
    return await HRService(db).update_payslip(_sid(user), payslip_id, body)


@router.get("/payroll/payslips", response_model=list[PayrollPayslipOut])
async def list_payslips(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_hr_admin)],
    run_id: UUID | None = None,
    status: str | None = Query(default=None, pattern=PAYSLIP_STATUS_PATTERN),
    teacher_id: UUID | None = None,
    year: int | None = Query(default=None, ge=2000, le=2100),
    q: str | None = Query(default=None, max_length=200),
):
    return await HRService(db).list_payslips(
        _sid(user), run_id=run_id, status=status, teacher_id=teacher_id, year=year, q=q
    )


@router.get("/payroll/payslips/me", response_model=list[PayrollPayslipOut])
async def list_my_payslips(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
    year: int | None = Query(default=None, ge=2000, le=2100),
):
    return await HRService(db).list_my_payslips(_sid(user), user.id, year=year)


@router.get("/payroll/payslips/{payslip_id}/pdf")
async def payslip_pdf(
    payslip_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    is_admin = user.role == UserRole.SCHOOL_ADMIN
    content, filename = await HRService(db).build_payslip_pdf(
        _sid(user),
        payslip_id,
        viewer_user_id=user.id,
        viewer_is_admin=is_admin,
    )
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

