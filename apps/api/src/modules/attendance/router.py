from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.attendance.schemas import AttendanceBulkCreate, AttendanceCreate, AttendanceOut, AttendanceUpdate
from src.modules.attendance.service import AttendanceService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/attendance", tags=["attendance"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[AttendanceOut])
async def list_attendance(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.read"))],
    page: PageQuery,
    class_id: UUID | None = None,
    student_id: UUID | None = None,
    attended_on: date | None = None,
):
    items, total = await AttendanceService(db).list(
        user, _school(user), page, class_id=class_id, student_id=student_id, attended_on=attended_on
    )
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=AttendanceOut, status_code=201)
async def mark_attendance(
    body: AttendanceCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.write"))],
):
    return await AttendanceService(db).mark(user, _school(user), body)


@router.post("/bulk", response_model=list[AttendanceOut], status_code=201)
async def mark_attendance_bulk(
    body: AttendanceBulkCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.write"))],
):
    return await AttendanceService(db).mark_many(user, _school(user), body.records)


@router.get("/{attendance_id}", response_model=AttendanceOut)
async def get_attendance(
    attendance_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.read"))],
):
    return await AttendanceService(db).get(user, _school(user), attendance_id)


@router.patch("/{attendance_id}", response_model=AttendanceOut)
async def update_attendance(
    attendance_id: UUID,
    body: AttendanceUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.write"))],
):
    return await AttendanceService(db).update(user, _school(user), attendance_id, body)
