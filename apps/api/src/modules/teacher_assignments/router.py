from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.shared.enums import UserRole
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


@router.get("/mine", response_model=list[TeacherAssignmentOut])
async def my_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await TeacherAssignmentService(db).list_mine(_sid(user), user.id)


@router.get("", response_model=list[TeacherAssignmentOut])
async def list_assignments(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    teacher_id: UUID | None = Query(None),
    academic_year_id: UUID | None = Query(None),
    class_id: UUID | None = Query(None),
):
    return await TeacherAssignmentService(db).list(
        _sid(user),
        teacher_id=teacher_id,
        academic_year_id=academic_year_id,
        class_id=class_id,
    )


@router.post("", response_model=TeacherAssignmentOut, status_code=201)
async def create_assignment(
    body: TeacherAssignmentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teacher_assignments.write"))],
):
    return await TeacherAssignmentService(db).create(_sid(user), body)


@router.delete("/{assignment_id}", status_code=200)
async def delete_assignment(
    assignment_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await TeacherAssignmentService(db).delete(_sid(user), assignment_id)
    return {"ok": True, "id": str(assignment_id)}
