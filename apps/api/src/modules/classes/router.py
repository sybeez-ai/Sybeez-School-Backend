from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.modules.access.deps import require_permission
from src.shared.enums import UserRole
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.classes.schemas import ClassCreate, ClassOut, ClassUpdate
from src.modules.classes.service import ClassService

router = APIRouter(prefix="/classes", tags=["classes"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[ClassOut])
async def list_classes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
):
    return await ClassService(db).list(_sid(user))


@router.post("", response_model=ClassOut, status_code=201)
async def create_class(
    body: ClassCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("classes.write"))],
):
    return await ClassService(db).create(_sid(user), body)


@router.patch("/{class_id}", response_model=ClassOut)
async def update_class(
    class_id: UUID,
    body: ClassUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ClassService(db).update(_sid(user), class_id, body)
