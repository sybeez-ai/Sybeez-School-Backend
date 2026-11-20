from datetime import date, time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.modules.access.deps import require_permission
from src.shared.enums import UserRole
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.timetable.schemas import (
    ChildBriefOut,
    PeriodCreate,
    PeriodOut,
    PeriodReorderRequest,
    PeriodUpdate,
    RoomCreate,
    RoomOut,
    RoomUpdate,
    TeacherPeriodOut,
    TimetableEntryCreate,
    TimetableEntryOut,
    TimetableEntryUpdate,
    TimetableGridOut,
    TimetableSettingsOut,
    TimetableSettingsUpdate,
    TodayScheduleOut,
)
from src.modules.timetable.service import TimetableService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/timetable", tags=["timetable"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


# ── settings ─────────────────────────────────────────────────


@router.get("/settings", response_model=TimetableSettingsOut)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await TimetableService(db).get_settings(_sid(user))


@router.patch("/settings", response_model=TimetableSettingsOut)
async def update_settings(
    body: TimetableSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).update_settings(_sid(user), body)


# ── periods ──────────────────────────────────────────────────


@router.get("/periods", response_model=list[PeriodOut])
async def list_periods(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
    enabled_only: bool = Query(False),
):
    return await TimetableService(db).list_periods(_sid(user), enabled_only=enabled_only)


@router.post("/periods", response_model=PeriodOut, status_code=201)
async def create_period(
    body: PeriodCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).create_period(_sid(user), body)


@router.patch("/periods/{period_id}", response_model=PeriodOut)
async def update_period(
    period_id: UUID,
    body: PeriodUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).update_period(_sid(user), period_id, body)


@router.delete("/periods/{period_id}", status_code=204)
async def delete_period(
    period_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await TimetableService(db).delete_period(_sid(user), period_id)


@router.post("/periods/reorder", response_model=list[PeriodOut])
async def reorder_periods(
    body: PeriodReorderRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).reorder_periods(_sid(user), body)


# ── rooms ────────────────────────────────────────────────────


@router.get("/rooms", response_model=list[RoomOut])
async def list_rooms(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    active_only: bool = Query(False),
):
    return await TimetableService(db).list_rooms(_sid(user), active_only=active_only)


@router.post("/rooms", response_model=RoomOut, status_code=201)
async def create_room(
    body: RoomCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).create_room(_sid(user), body)


@router.patch("/rooms/{room_id}", response_model=RoomOut)
async def update_room(
    room_id: UUID,
    body: RoomUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).update_room(_sid(user), room_id, body)


@router.delete("/rooms/{room_id}", status_code=204)
async def delete_room(
    room_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await TimetableService(db).delete_room(_sid(user), room_id)


# ── role views (before /{id}) ────────────────────────────────


@router.get("/mine", response_model=list[TeacherPeriodOut])
async def my_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
    on_date: date | None = Query(None),
    as_of: time | None = Query(None),
):
    result = await TimetableService(db).list_for_teacher(
        school_id=_sid(user),
        user_id=user.id,
        on_date=on_date,
        as_of=as_of,
    )
    return result


@router.get("/teacher", response_model=list[TimetableEntryOut])
async def teacher_week(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    result = await TimetableService(db).list_for_teacher(
        school_id=_sid(user), user_id=user.id, week=True
    )
    return result


@router.get("/student", response_model=list[TimetableEntryOut])
async def student_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await TimetableService(db).list_for_student(school_id=_sid(user), user_id=user.id)


@router.get("/parent/children", response_model=list[ChildBriefOut])
async def parent_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await TimetableService(db).list_children(school_id=_sid(user), user_id=user.id)


@router.get("/parent", response_model=list[TimetableEntryOut])
async def parent_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
    student_id: UUID = Query(...),
):
    return await TimetableService(db).list_for_parent_child(
        school_id=_sid(user), user_id=user.id, student_id=student_id
    )


@router.get("/today", response_model=TodayScheduleOut)
async def today_schedule(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.SCHOOL_ADMIN,
                UserRole.TEACHER,
                UserRole.STUDENT,
                UserRole.PARENT,
            )
        ),
    ],
    on_date: date | None = Query(None),
    student_id: UUID | None = Query(None),
    as_of: time | None = Query(None),
):
    return await TimetableService(db).today(
        school_id=_sid(user),
        user_id=user.id,
        role=user.role.value,
        on_date=on_date,
        student_id=student_id,
        as_of=as_of,
    )


@router.get("/grid", response_model=TimetableGridOut)
async def timetable_grid(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
    class_id: UUID = Query(...),
):
    return await TimetableService(db).grid(school_id=_sid(user), class_id=class_id, user=user)


# ── entries ──────────────────────────────────────────────────


@router.get("", response_model=list[TimetableEntryOut])
async def list_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.read"))],
    class_id: UUID | None = Query(None),
    teacher_id: UUID | None = Query(None),
    academic_year_id: UUID | None = Query(None),
    day_of_week: int | None = Query(None, ge=0, le=6),
):
    return await TimetableService(db).list_entries(
        school_id=_sid(user),
        class_id=class_id,
        teacher_id=teacher_id,
        academic_year_id=academic_year_id,
        day_of_week=day_of_week,
        user=user,
    )


@router.post("", response_model=TimetableEntryOut, status_code=201)
async def create_entry(
    body: TimetableEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.write"))],
):
    return await TimetableService(db).create_entry(_sid(user), body)


@router.patch("/{entry_id}", response_model=TimetableEntryOut)
async def update_entry(
    entry_id: UUID,
    body: TimetableEntryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TimetableService(db).update_entry(_sid(user), entry_id, body)


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await TimetableService(db).delete_entry(_sid(user), entry_id)
