from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.teachers.schemas import TeacherCreate, TeacherOut
from src.modules.teachers.service import TeacherService
from src.shared.enums import UserRole

router = APIRouter(prefix="/teachers", tags=["teachers"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[TeacherOut])
async def list_teachers(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherService(db).list(_sid(user))


@router.post("", response_model=TeacherOut, status_code=201)
async def create_teacher(
    body: TeacherCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherService(db).create(_sid(user), body)
