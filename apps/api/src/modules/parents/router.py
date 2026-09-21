from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.parents.schemas import LinkParentRequest, ParentCreate, ParentOut
from src.modules.parents.service import ParentService
from src.shared.enums import UserRole

router = APIRouter(prefix="/parents", tags=["parents"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("", response_model=list[ParentOut])
async def list_parents(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ParentService(db).list(_sid(user))


@router.post("", response_model=ParentOut, status_code=201)
async def create_parent(
    body: ParentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ParentService(db).create(_sid(user), body)


@router.get("/by-student/{student_id}", response_model=list[ParentOut])
async def parents_for_student(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ParentService(db).list_for_student(_sid(user), student_id)


@router.post("/by-student/{student_id}/link", status_code=201)
async def link_parent(
    student_id: UUID,
    body: LinkParentRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    link = await ParentService(db).link_to_student(_sid(user), student_id, body)
    return {"id": str(link.id), "student_id": str(link.student_id), "parent_id": str(link.parent_id), "relationship": link.relation_type}
