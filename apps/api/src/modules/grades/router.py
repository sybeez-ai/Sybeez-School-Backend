from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.grades.schemas import GradeCreate, GradeOut, GradeUpdate
from src.modules.grades.service import GradeService
from src.shared.enums import UserRole

router = APIRouter(prefix="/grades", tags=["grades"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[GradeOut])
async def list_grades(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
    active_only: bool = Query(default=False),
):
    return await GradeService(db).list(_sid(user), active_only=active_only)


@router.post("", response_model=GradeOut, status_code=201)
async def create_grade(
    body: GradeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await GradeService(db).create(_sid(user), body)


@router.patch("/{grade_id}", response_model=GradeOut)
async def update_grade(
    grade_id: UUID,
    body: GradeUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await GradeService(db).update(_sid(user), grade_id, body)
