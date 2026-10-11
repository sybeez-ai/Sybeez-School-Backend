from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.teachers.schemas import TeacherCreate, TeacherOut, TeacherUpdate
from src.modules.teachers.service import TeacherService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/teachers", tags=["teachers"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[TeacherOut])
async def list_teachers(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.read"))],
    page: PageQuery,
):
    items, total = await TeacherService(db).list(_sid(user), page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=TeacherOut, status_code=201)
async def create_teacher(
    body: TeacherCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.write"))],
):
    return await TeacherService(db).create(_sid(user), body, actor_id=user.id)


@router.get("/me", response_model=TeacherOut)
async def my_teacher(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.read"))],
):
    return await TeacherService(db).me(user, _sid(user))


@router.get("/{teacher_id}", response_model=TeacherOut)
async def get_teacher(
    teacher_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.read"))],
):
    return await TeacherService(db).get(_sid(user), teacher_id)


@router.patch("/{teacher_id}", response_model=TeacherOut)
async def update_teacher(
    teacher_id: UUID,
    body: TeacherUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("teachers.write"))],
):
    return await TeacherService(db).update(_sid(user), teacher_id, body)
