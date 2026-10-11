import calendar
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import AppError, ForbiddenError
from src.modules.staff_attendance.schemas import (
    AttendanceBulk,
    AttendanceMark,
    AttendanceOut,
    AttendanceUpdate,
    BulkResult,
    DailySheet,
    RangeSummary,
)
from src.modules.staff_attendance.service import StaffAttendanceService
router = APIRouter(prefix="/hr/attendance", tags=["hr-attendance"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]
Q = Annotated[str | None, Query(max_length=200)]
StaffType = Annotated[str | None, Query(pattern="^(TEACHING|NON_TEACHING)$")]
DepartmentId = Annotated[UUID | None, Query()]
MarkStatus = Annotated[str | None, Query(pattern="^(PRESENT|ABSENT|HALF_DAY|LEAVE)$")]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


def _range(
    date_from: date | None, date_to: date | None, month: str | None
) -> tuple[date, date]:
    if month:
        year, mon = (int(p) for p in month.split("-"))
        if not 1 <= mon <= 12:
            raise AppError("month must be YYYY-MM", status_code=422)
        return date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])
    if date_from is None or date_to is None:
        raise AppError("Provide date_from and date_to, or month", status_code=422)
    return date_from, date_to


@router.get("/daily", response_model=DailySheet)
async def daily_sheet(
    user: Admin,
    db: Db,
    attendance_date: Annotated[date, Query(alias="date")],
    q: Q = None,
    staff_type: StaffType = None,
    department_id: DepartmentId = None,
    status: Annotated[
        str | None, Query(pattern="^(PRESENT|ABSENT|HALF_DAY|LEAVE|UNMARKED)$")
    ] = None,
):
    return await StaffAttendanceService(db).daily(
        _sid(user),
        attendance_date,
        q=q,
        staff_type=staff_type,
        department_id=department_id,
        status=status,
    )


@router.get("", response_model=list[AttendanceOut])
async def attendance_history(
    user: Admin,
    db: Db,
    date_from: date | None = None,
    date_to: date | None = None,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    employee_id: UUID | None = None,
    status: MarkStatus = None,
    q: Q = None,
    staff_type: StaffType = None,
    department_id: DepartmentId = None,
):
    start, end = _range(date_from, date_to, month)
    return await StaffAttendanceService(db).history(
        _sid(user),
        date_from=start,
        date_to=end,
        employee_id=employee_id,
        status=status,
        q=q,
        staff_type=staff_type,
        department_id=department_id,
    )


@router.get("/summary", response_model=RangeSummary)
async def attendance_summary(
    user: Admin,
    db: Db,
    date_from: date | None = None,
    date_to: date | None = None,
    month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    q: Q = None,
    staff_type: StaffType = None,
    department_id: DepartmentId = None,
):
    start, end = _range(date_from, date_to, month)
    return await StaffAttendanceService(db).summary(
        _sid(user),
        date_from=start,
        date_to=end,
        q=q,
        staff_type=staff_type,
        department_id=department_id,
    )


@router.post("", response_model=AttendanceOut, status_code=201)
async def mark_attendance(body: AttendanceMark, user: Admin, db: Db):
    return await StaffAttendanceService(db).mark(_sid(user), user.id, body)


@router.post("/bulk", response_model=BulkResult)
async def bulk_attendance(body: AttendanceBulk, user: Admin, db: Db):
    return await StaffAttendanceService(db).bulk(_sid(user), user.id, body)


@router.patch("/{record_id}", response_model=AttendanceOut)
async def update_attendance(
    record_id: UUID, body: AttendanceUpdate, user: Admin, db: Db
):
    return await StaffAttendanceService(db).update(_sid(user), user.id, record_id, body)


@router.delete("/{record_id}", status_code=204)
async def delete_attendance(record_id: UUID, user: Admin, db: Db):
    await StaffAttendanceService(db).delete(_sid(user), record_id)
    return Response(status_code=204)
