from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.students.schemas import StudentCreate, StudentOut
from src.modules.students.service import StudentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/students", tags=["students"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[StudentOut])
async def list_students(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))],
):
    return await StudentService(db).list(_sid(user))


@router.post("", response_model=StudentOut, status_code=201)
async def create_student(
    body: StudentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await StudentService(db).create(_sid(user), body)
