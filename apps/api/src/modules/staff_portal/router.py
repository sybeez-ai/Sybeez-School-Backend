"""Employee self-service. A person sees only the payslips linked to their HR record."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import NotFoundError
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.modules.payroll.service import PayrollService, dump

router = APIRouter(prefix="/staff", tags=["staff-self-service"])
Db = Annotated[AsyncSession, Depends(get_db)]
Staff = Annotated[CurrentUser, Depends(require_roles(*SELF_SERVICE_ROLES))]


def _svc(db: AsyncSession, user: CurrentUser) -> PayrollService:
    return PayrollService(db, user)


@router.get("/me/payslips")
async def my_payslips(
    user: Staff,
    db: Db,
    year: int | None = Query(default=None, ge=2000, le=2100),
):
    """Published and paid payslips for the signed-in employee. Drafts stay hidden."""
    rows = await _svc(db, user).my_payslips(year)
    return [dump(row) for row in rows]


@router.get("/me/payslips/{payslip_id}")
async def my_payslip(payslip_id: UUID, user: Staff, db: Db):
    rows = await _svc(db, user).my_payslips(None)
    for row in rows:
        if row.id == payslip_id:
            return dump(row)
    raise NotFoundError("Payslip not found")


@router.get("/me/payslips/{payslip_id}/pdf")
async def my_payslip_pdf(payslip_id: UUID, user: Staff, db: Db):
    content, name = await _svc(db, user).payslip_pdf(payslip_id, intent="view")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{name}"'},
    )
