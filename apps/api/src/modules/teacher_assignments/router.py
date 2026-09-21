from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.teacher_assignments.schemas import TeacherAssignmentCreate, TeacherAssignmentOut
from src.modules.teacher_assignments.service import TeacherAssignmentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/teacher-assignments", tags=["teacher_assignments"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[TeacherAssignmentOut])
async def list_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherAssignmentService(db).list(_sid(user))


@router.post("", response_model=TeacherAssignmentOut, status_code=201)
async def create_assignment(
    body: TeacherAssignmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await TeacherAssignmentService(db).create(_sid(user), body)
