from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.access.deps import require_permission
from src.shared.enums import UserRole
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.teachers.schemas import (
    TeacherCreate,
    TeacherDashboardOut,
    TeacherOut,
    TeacherStatusUpdate,
    TeacherUpdate,
)
from src.modules.teachers.service import TeacherService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/teachers", tags=["teachers"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("/me", response_model=TeacherOut)
async def my_teacher_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await TeacherService(db).get_me(_sid(user), user.id)


@router.get("/me/dashboard", response_model=TeacherDashboardOut)
async def my_teacher_dashboard(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await TeacherService(db).dashboard(_sid(user), user.id)


@router.get("", response_model=list[TeacherOut])
async def list_teachers(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(
                UserRole.SCHOOL_ADMIN,
                UserRole.HR,
                UserRole.HR_MANAGER,
                UserRole.PAYROLL_ADMIN,
                UserRole.PAYROLL_APPROVER,
            )
        ),
    ],
    q: str | None = Query(None),
    status: str | None = Query(None),
    academic_year_id: UUID | None = Query(None),
):
    return await TeacherService(db).list(
        _sid(user), q=q, status=status, academic_year_id=academic_year_id
    )


@router.post("", response_model=TeacherOut, status_code=201)
async def create_teacher(
    body: TeacherCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.write"))],
):
    return await TeacherService(db).create(_sid(user), body)


@router.get("/{teacher_id}", response_model=TeacherOut)
async def get_teacher(
    teacher_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    svc = TeacherService(db)
    school_id = _sid(user)
    if user.role == UserRole.TEACHER:
        await svc.assert_teacher_owns_user(school_id, user.id, teacher_id)
    return await svc.get(school_id, teacher_id)


@router.patch("/{teacher_id}", response_model=TeacherOut)
async def update_teacher(
    teacher_id: UUID,
    body: TeacherUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherService(db).update(_sid(user), teacher_id, body)


@router.patch("/{teacher_id}/status", response_model=TeacherOut)
async def update_teacher_status(
    teacher_id: UUID,
    body: TeacherStatusUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherService(db).set_status(
        _sid(user), teacher_id, status=body.status, is_active=body.is_active
    )


@router.post("/{teacher_id}/photo", response_model=TeacherOut)
async def upload_teacher_photo(
    teacher_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    file: UploadFile = File(...),
):
    return await TeacherService(db).upload_photo(_sid(user), teacher_id, file)
