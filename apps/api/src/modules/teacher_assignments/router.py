from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.teacher_assignments.schemas import TeacherAssignmentCreate, TeacherAssignmentOut
from src.modules.teacher_assignments.service import TeacherAssignmentService

router = APIRouter(prefix="/teacher-assignments", tags=["teacher_assignments"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[TeacherAssignmentOut])
async def list_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teacher_assignments.read"))],
):
    return await TeacherAssignmentService(db).list(_sid(user))


@router.post("", response_model=TeacherAssignmentOut, status_code=201)
async def create_assignment(
    body: TeacherAssignmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teacher_assignments.write"))],
):
    return await TeacherAssignmentService(db).create(_sid(user), body)
