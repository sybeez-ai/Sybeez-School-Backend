from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.modules.school_ops.service import SchoolOpsService
from src.shared.enums import UserRole

router = APIRouter(prefix="/ops", tags=["school-ops"])
DB = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]


class ConfirmBody(BaseModel):
    timetable_entry_id: UUID
    date: date
    substitute_teacher_id: UUID
    note: str | None = Field(default=None, max_length=500)


@router.get("/substitutes/candidates")
async def candidates(db: DB, user: Admin, teacher_id: UUID, on: date):
    return await SchoolOpsService(db).candidates(user, teacher_id, on)


@router.get("/substitutes")
async def covers(db: DB, user: Admin, on: date = Query(default_factory=date.today)):
    return await SchoolOpsService(db).list_covers(user, on)


@router.post("/substitutes", status_code=201)
async def confirm(body: ConfirmBody, db: DB, user: Admin):
    """The admin's explicit confirmation. Nothing is assigned or sent before this call."""
    row = await SchoolOpsService(db).confirm(
        user,
        entry_id=body.timetable_entry_id,
        on=body.date,
        substitute_teacher_id=body.substitute_teacher_id,
        note=body.note,
    )
    return {"id": str(row.id), "status": row.status}


@router.delete("/substitutes/{assignment_id}", status_code=204)
async def cancel(assignment_id: UUID, db: DB, user: Admin):
    await SchoolOpsService(db).cancel(user, assignment_id)


@router.get("/workload")
async def workload(db: DB, user: Admin, start: date, end: date):
    return await SchoolOpsService(db).workload(user, start, end)


@router.get("/principal-report")
async def principal_report(db: DB, user: Admin, year: int = Query(ge=2020, le=2100), month: int = Query(ge=1, le=12)):
    return await SchoolOpsService(db).monthly_report(user, year, month)


@router.get("/principal-report.pdf")
async def principal_report_pdf(db: DB, user: Admin, year: int = Query(ge=2020, le=2100), month: int = Query(ge=1, le=12)):
    from src.modules.alerts.service import school_display_name
    from src.modules.payroll.pdf import _document

    svc = SchoolOpsService(db)
    rep = await svc.monthly_report(user, year, month)
    lines = svc.report_lines(await school_display_name(db, user.school_id), rep)
    return Response(
        _document(lines[:48], None, None),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="principal-report-{rep["month"]}.pdf"'},
    )
