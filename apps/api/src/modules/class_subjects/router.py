from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.class_subjects.schemas import ClassSubjectCreate, ClassSubjectOut, ClassSubjectUpdate
from src.modules.class_subjects.service import ClassSubjectService
from src.shared.enums import UserRole

router = APIRouter(prefix="/classes", tags=["class-subjects"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("/{class_id}/subjects", response_model=list[ClassSubjectOut])
async def list_class_subjects(
    class_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await ClassSubjectService(db).list_for_class(_sid(user), class_id)


@router.post("/{class_id}/subjects", response_model=ClassSubjectOut, status_code=status.HTTP_201_CREATED)
async def add_class_subject(
    class_id: UUID,
    body: ClassSubjectCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("subjects.write"))],
):
    return await ClassSubjectService(db).add(_sid(user), class_id, body)


@router.patch("/{class_id}/subjects/{item_id}", response_model=ClassSubjectOut)
async def update_class_subject(
    class_id: UUID,
    item_id: UUID,
    body: ClassSubjectUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ClassSubjectService(db).update(_sid(user), class_id, item_id, body)


@router.delete("/{class_id}/subjects/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class_subject(
    class_id: UUID,
    item_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await ClassSubjectService(db).remove(_sid(user), class_id, item_id)
