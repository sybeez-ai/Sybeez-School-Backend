from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.access.deps import require_permission
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.db.models import Student
from src.modules.analytics.schemas import (
    AnalyticsFilters,
    AnalyticsSettingsOut,
    AnalyticsSettingsUpdate,
)
from src.modules.analytics.service import AnalyticsService
from src.shared.enums import UserRole

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


def _filters(
    academic_year_id: UUID | None = None,
    grade_id: UUID | None = None,
    section_id: UUID | None = None,
    subject_id: UUID | None = None,
    exam_id: UUID | None = None,
    from_date: date | None = Query(None, alias="from"),
    to_date: date | None = Query(None, alias="to"),
) -> AnalyticsFilters:
    return AnalyticsFilters(
        academic_year_id=academic_year_id,
        grade_id=grade_id,
        section_id=section_id,
        subject_id=subject_id,
        exam_id=exam_id,
        from_date=from_date,
        to_date=to_date,
    )


@router.get("/health")
async def analytics_health(_: Annotated[CurrentUser, Depends(require_permission("analytics.read"))]):
    return {"module": "analytics", "status": "ok"}


@router.get("/overview")
async def overview(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).overview(_sid(user), f)


@router.get("/academic")
async def academic(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).academic(_sid(user), f)


@router.get("/students")
async def students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).students(_sid(user), f)


@router.get("/attendance")
async def attendance(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    svc = AnalyticsService(db)
    if user.role.value == UserRole.TEACHER.value:
        allowed = await svc._teacher_class_ids(_sid(user), user.id)
        if f.section_id and f.section_id not in allowed:
            raise ForbiddenError("Not assigned to this class")
        if not f.section_id and allowed:
            f = f.model_copy(update={"section_id": allowed[0]})
    return await svc.attendance(_sid(user), f)


@router.get("/homework")
async def homework(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).homework(_sid(user), f)


@router.get("/exams")
async def exams(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).exams(_sid(user), f)


@router.get("/fees")
async def fees(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).fees(_sid(user), f)


@router.get("/communication")
async def communication(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    return await AnalyticsService(db).communication(_sid(user), f)


@router.get("/classes")
async def classes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    svc = AnalyticsService(db)
    if user.role.value == UserRole.TEACHER.value:
        return (await svc.teacher(_sid(user), user.id, f)).classes
    return await svc.classes(_sid(user), f)


@router.get("/classes/{section_id}")
async def class_detail(
    section_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    svc = AnalyticsService(db)
    if user.role.value == UserRole.TEACHER.value:
        allowed = await svc._teacher_class_ids(_sid(user), user.id)
        if section_id not in allowed:
            raise ForbiddenError("Not assigned to this class")
    return await svc.class_detail(_sid(user), section_id, f)


@router.get("/students/{student_id}")
async def student_analytics(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.SCHOOL_ADMIN,
                UserRole.TEACHER,
                UserRole.PARENT,
                UserRole.STUDENT,
            )
        ),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    svc = AnalyticsService(db)
    await svc.assert_student_access(_sid(user), user.id, user.role.value, student_id)
    return await svc.student(_sid(user), student_id, f)


@router.get("/me")
async def my_analytics(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.STUDENT, UserRole.PARENT, UserRole.TEACHER)),
    ],
    f: Annotated[AnalyticsFilters, Depends(_filters)],
):
    svc = AnalyticsService(db)
    if user.role.value == UserRole.TEACHER.value:
        return await svc.teacher(_sid(user), user.id, f)
    if user.role.value == UserRole.PARENT.value:
        kids = await svc._linked_children(_sid(user), user.id)
        return [await svc.student(_sid(user), kid, f) for kid in kids]
    st = (
        await db.execute(
            select(Student).where(Student.school_id == _sid(user), Student.user_id == user.id)
        )
    ).scalar_one_or_none()
    if not st:
        raise ForbiddenError("Student profile required")
    return await svc.student(_sid(user), st.id, f)


@router.get("/settings", response_model=AnalyticsSettingsOut)
async def get_settings(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    row = await AnalyticsService(db).get_settings(_sid(user))
    await db.commit()
    return AnalyticsSettingsOut.model_validate(row)


@router.patch("/settings", response_model=AnalyticsSettingsOut)
async def update_settings(
    body: AnalyticsSettingsUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AnalyticsService(db).update_settings(_sid(user), body)
