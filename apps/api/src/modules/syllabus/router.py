from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.syllabus.schemas import (
    SyllabusCreate,
    SyllabusOut,
    SyllabusTopicCreate,
    SyllabusTopicOut,
)
from src.modules.syllabus.service import SyllabusService
from src.shared.enums import UserRole

router = APIRouter(prefix="/syllabus", tags=["syllabus"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


async def _visible(svc: SyllabusService, user: CurrentUser, syllabus_id: UUID) -> None:
    """Students and parents only open a syllabus for their own class."""
    if user.role == UserRole.STUDENT:
        allowed = {row.id for row in await svc.list_for_student(_sid(user), user.id)}
        if syllabus_id not in allowed:
            raise ForbiddenError("Not your class syllabus")
    elif user.role == UserRole.PARENT:
        allowed = {row.id for row in await svc.list_for_parent(_sid(user), user.id)}
        if syllabus_id not in allowed:
            raise ForbiddenError("Not a linked child's syllabus")


@router.get("", response_model=list[SyllabusOut])
async def list_syllabus(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
    ],
):
    svc = SyllabusService(db)
    school_id = _sid(user)
    if user.role == UserRole.STUDENT:
        return await svc.list_for_student(school_id, user.id)
    if user.role == UserRole.PARENT:
        return await svc.list_for_parent(school_id, user.id)
    return await svc.list(school_id)


@router.get("/mine", response_model=list[SyllabusOut])
async def my_syllabus(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT))],
):
    return await SyllabusService(db).list_for_student(_sid(user), user.id)


@router.post("", response_model=SyllabusOut, status_code=201)
async def create_syllabus(
    body: SyllabusCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await SyllabusService(db).create(_sid(user), body)


@router.get("/{syllabus_id}", response_model=SyllabusOut)
async def get_syllabus(
    syllabus_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
    ],
):
    svc = SyllabusService(db)
    await _visible(svc, user, syllabus_id)
    return await svc.get(_sid(user), syllabus_id)


@router.get("/{syllabus_id}/topics", response_model=list[SyllabusTopicOut])
async def list_topics(
    syllabus_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT, UserRole.PARENT)),
    ],
):
    svc = SyllabusService(db)
    await _visible(svc, user, syllabus_id)
    return await svc.list_topics(_sid(user), syllabus_id)


@router.post("/{syllabus_id}/topics", response_model=SyllabusTopicOut, status_code=201)
async def add_topic(
    syllabus_id: UUID,
    body: SyllabusTopicCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await SyllabusService(db).add_topic(_sid(user), syllabus_id, body)
