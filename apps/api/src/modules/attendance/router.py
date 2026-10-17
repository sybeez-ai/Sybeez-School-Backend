from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.access.deps import require_permission
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.attendance.permissions import READ_ROLES, WRITE_ROLES
from src.modules.attendance.schemas import (
    AlertOut,
    AttendanceMarkRequest,
    AttendanceOut,
    AttendanceSettingsOut,
    AttendanceSettingsUpdate,
    AttendanceSummaryOut,
    AuditLogOut,
    ChildOut,
    ClassRateOut,
    LeaveDecisionRequest,
    LeaveRequestCreate,
    LeaveRequestOut,
    MonthlyStatsOut,
    OverviewOut,
    RosterStudentOut,
    StudentRateOut,
)
from src.modules.attendance.service import AttendanceService
from src.modules.reports.exporters import rows_to_csv
from src.shared.enums import UserRole
from src.shared.pagination import Page, PageQuery
from src.modules.access.deps import public

router = APIRouter(prefix="/attendance", tags=["attendance"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=Page[AttendanceOut])
async def list_attendance(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("attendance.read"))],
    page: PageQuery,
):
    items, total = await AttendanceService(db).list_visible(user, _sid(user), page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/health")
@public
async def attendance_health():
    return {"module": "attendance", "status": "ok"}


@router.get("/settings", response_model=AttendanceSettingsOut)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await AttendanceService(db).get_settings(_sid(user))


@router.patch("/settings", response_model=AttendanceSettingsOut)
async def update_settings(
    body: AttendanceSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AttendanceService(db).update_settings(_sid(user), body)


@router.get("/overview", response_model=OverviewOut)
async def overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    attended_on: date = Query(default_factory=date.today),
):
    return await AttendanceService(db).overview(school_id=_sid(user), attended_on=attended_on)


@router.get("/alerts", response_model=list[AlertOut])
async def alerts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await AttendanceService(db).alerts(school_id=_sid(user))


@router.get("/reports/students", response_model=list[StudentRateOut])
async def report_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    date_from: date = Query(...),
    date_to: date = Query(...),
    class_id: UUID | None = Query(None),
    format: str | None = Query(default=None, description="When set to 'csv', returns a CSV download."),
    kind: str = Query(default="all", description="all | absence | late"),
):
    rows = await AttendanceService(db).report_students(
        school_id=_sid(user), date_from=date_from, date_to=date_to, class_id=class_id
    )
    if (format or "").lower() != "csv":
        return rows
    k = (kind or "all").lower().strip()
    if k == "absence":
        columns = ["student", "class", "absences", "rate_percent"]
        data = [
            {
                "student": r.student_name,
                "class": r.class_name or "",
                "absences": r.absent,
                "rate_percent": r.rate_percent,
            }
            for r in rows
            if r.absent
        ]
        filename = f"absence-report-{date_from}-{date_to}.csv"
    elif k == "late":
        columns = ["student", "class", "late", "rate_percent"]
        data = [
            {
                "student": r.student_name,
                "class": r.class_name or "",
                "late": r.late,
                "rate_percent": r.rate_percent,
            }
            for r in rows
            if r.late
        ]
        filename = f"late-report-{date_from}-{date_to}.csv"
    else:
        columns = ["student", "class", "present", "absent", "late", "excused", "half_day", "total", "rate_percent"]
        data = [
            {
                "student": r.student_name,
                "class": r.class_name or "",
                "present": r.present,
                "absent": r.absent,
                "late": r.late,
                "excused": r.excused,
                "half_day": r.half_day,
                "total": r.total,
                "rate_percent": r.rate_percent,
            }
            for r in rows
        ]
        filename = f"attendance-students-{date_from}-{date_to}.csv"
    return Response(
        content=rows_to_csv(data, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/classes", response_model=list[ClassRateOut])
async def report_classes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    date_from: date = Query(...),
    date_to: date = Query(...),
    format: str | None = Query(default=None, description="When set to 'csv', returns a CSV download."),
):
    rows = await AttendanceService(db).report_classes(school_id=_sid(user), date_from=date_from, date_to=date_to)
    if (format or "").lower() != "csv":
        return rows
    columns = ["class", "present", "absent", "late", "excused", "half_day", "total", "rate_percent"]
    data = [
        {
            "class": r.class_name,
            "present": r.present,
            "absent": r.absent,
            "late": r.late,
            "excused": r.excused,
            "half_day": r.half_day,
            "total": r.total,
            "rate_percent": r.rate_percent,
        }
        for r in rows
    ]
    filename = f"attendance-classes-{date_from}-{date_to}.csv"
    return Response(
        content=rows_to_csv(data, columns),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/digests/absence/run")
async def run_absence_digest(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    attended_on: date = Query(default_factory=date.today),
):
    return await AttendanceService(db).send_daily_absence_digests(school_id=_sid(user), attended_on=attended_on)


@router.get("/children", response_model=list[ChildOut])
async def list_my_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await AttendanceService(db).list_children(school_id=_sid(user), user_id=user.id)


@router.get("/roster", response_model=list[RosterStudentOut])
async def get_roster(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*WRITE_ROLES))],
    class_id: UUID = Query(...),
    attended_on: date = Query(...),
):
    roster, _ = await AttendanceService(db).roster(
        school_id=_sid(user),
        class_id=class_id,
        attended_on=attended_on,
        user_id=user.id,
        role=user.role.value,
    )
    return roster


@router.get("/summary", response_model=AttendanceSummaryOut)
async def get_summary(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*WRITE_ROLES))],
    class_id: UUID = Query(...),
    attended_on: date = Query(...),
):
    _, summary = await AttendanceService(db).roster(
        school_id=_sid(user),
        class_id=class_id,
        attended_on=attended_on,
        user_id=user.id,
        role=user.role.value,
    )
    return summary


@router.post("/mark", response_model=list[AttendanceOut])
async def mark_attendance(
    body: AttendanceMarkRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*WRITE_ROLES))],
):
    return await AttendanceService(db).mark(
        school_id=_sid(user),
        user_id=user.id,
        data=body,
        role=user.role.value,
    )


@router.get("/me", response_model=list[AttendanceOut])
async def my_attendance(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    svc = AttendanceService(db)
    student = await svc.student_for_user(school_id=_sid(user), user_id=user.id)
    return await svc.list_for_student(
        school_id=_sid(user),
        student_id=student.id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/me/monthly", response_model=MonthlyStatsOut)
async def my_monthly(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    svc = AttendanceService(db)
    student = await svc.student_for_user(school_id=_sid(user), user_id=user.id)
    return await svc.monthly_stats(
        school_id=_sid(user), student_id=student.id, year=year, month=month
    )


@router.get("/student/{student_id}", response_model=list[AttendanceOut])
async def student_attendance(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*READ_ROLES))],
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    svc = AttendanceService(db)
    school_id = _sid(user)
    if user.role == UserRole.STUDENT:
        me = await svc.student_for_user(school_id=school_id, user_id=user.id)
        if me.id != student_id:
            raise ForbiddenError("Cannot view another student's attendance")
    elif user.role == UserRole.PARENT:
        await svc.assert_parent_of(school_id=school_id, user_id=user.id, student_id=student_id)
    elif user.role == UserRole.TEACHER:
        await svc.assert_teacher_of_student(school_id=school_id, user_id=user.id, student_id=student_id)
    return await svc.list_for_student(
        school_id=school_id,
        student_id=student_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get("/student/{student_id}/monthly", response_model=MonthlyStatsOut)
async def student_monthly(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*READ_ROLES))],
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    svc = AttendanceService(db)
    school_id = _sid(user)
    if user.role == UserRole.STUDENT:
        me = await svc.student_for_user(school_id=school_id, user_id=user.id)
        if me.id != student_id:
            raise ForbiddenError("Cannot view another student's attendance")
    elif user.role == UserRole.PARENT:
        await svc.assert_parent_of(school_id=school_id, user_id=user.id, student_id=student_id)
    elif user.role == UserRole.TEACHER:
        await svc.assert_teacher_of_student(school_id=school_id, user_id=user.id, student_id=student_id)
    return await svc.monthly_stats(
        school_id=school_id, student_id=student_id, year=year, month=month
    )


@router.get("/student/{student_id}/audit", response_model=list[AuditLogOut])
async def student_audit(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.PARENT))],
):
    svc = AttendanceService(db)
    school_id = _sid(user)
    if user.role == UserRole.PARENT:
        await svc.assert_parent_of(school_id=school_id, user_id=user.id, student_id=student_id)
    elif user.role == UserRole.TEACHER:
        await svc.assert_teacher_of_student(school_id=school_id, user_id=user.id, student_id=student_id)
    return await svc.audit_for_student(school_id=school_id, student_id=student_id)


@router.get("/leave-requests", response_model=list[LeaveRequestOut])
async def list_leave_requests(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*READ_ROLES))],
    status: str | None = Query(None),
    student_id: UUID | None = Query(None),
):
    return await AttendanceService(db).list_leaves(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        status=status,
        student_id=student_id,
    )


@router.post("/leave-requests", response_model=LeaveRequestOut, status_code=201)
async def create_leave_request(
    body: LeaveRequestCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.STUDENT,
                UserRole.PARENT,
                UserRole.SCHOOL_ADMIN,
                UserRole.TEACHER,
            )
        ),
    ],
):
    return await AttendanceService(db).create_leave(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        data=body,
    )


@router.post("/leave-requests/{leave_id}/decide", response_model=LeaveRequestOut)
async def decide_leave_request(
    leave_id: UUID,
    body: LeaveDecisionRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await AttendanceService(db).decide_leave(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        leave_id=leave_id,
        data=body,
    )


@router.post("/leave-requests/{leave_id}/cancel", response_model=LeaveRequestOut)
async def cancel_leave_request(
    leave_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.STUDENT,
                UserRole.PARENT,
                UserRole.SCHOOL_ADMIN,
                UserRole.TEACHER,
            )
        ),
    ],
):
    return await AttendanceService(db).cancel_leave(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        leave_id=leave_id,
    )
