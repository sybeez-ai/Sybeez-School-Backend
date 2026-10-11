from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.parents.schemas import LinkParentRequest, ParentCreate, ParentOut
from src.modules.parents.service import ParentService
from src.modules.students.schemas import StudentOut
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/parents", tags=["parents"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[ParentOut])
async def list_parents(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("parents.read"))],
    page: PageQuery,
):
    items, total = await ParentService(db).list(_sid(user), page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=ParentOut, status_code=201)
async def create_parent(
    body: ParentCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("parents.write"))],
):
    return await ParentService(db).create(_sid(user), body, actor_id=user.id)


@router.get("/me", response_model=ParentOut)
async def my_parent(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("parents.read"))],
):
    return await ParentService(db).me(user, _sid(user))


@router.get("/me/children", response_model=list[StudentOut])
async def my_children(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("students.read"))],
):
    return await ParentService(db).my_children(user, _sid(user))


@router.get("/by-student/{student_id}", response_model=list[ParentOut])
async def parents_for_student(
    student_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("parents.read"))],
):
    return await ParentService(db).list_for_student(_sid(user), student_id)


@router.post("/by-student/{student_id}/link", status_code=201)
async def link_parent(
    student_id: UUID,
    body: LinkParentRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("parents.write"))],
):
    link = await ParentService(db).link_to_student(_sid(user), student_id, body)
    return {
        "id": str(link.id),
        "student_id": str(link.student_id),
        "parent_id": str(link.parent_id),
        "relationship": link.relation_type,
    }
