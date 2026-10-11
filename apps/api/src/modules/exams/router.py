from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.exams.schemas import ExamCreate, ExamOut, ExamUpdate
from src.modules.exams.service import ExamService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/exams", tags=["exams"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[ExamOut])
async def list_exams(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("exams.read"))],
    page: PageQuery,
    class_id: UUID | None = None,
):
    items, total = await ExamService(db).list(user, _school(user), page, class_id)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=ExamOut, status_code=201)
async def create_exam(
    body: ExamCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("exams.write"))],
):
    return await ExamService(db).create(_school(user), body)


@router.get("/{exam_id}", response_model=ExamOut)
async def get_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("exams.read"))],
):
    return await ExamService(db).get(user, _school(user), exam_id)


@router.patch("/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: UUID,
    body: ExamUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("exams.write"))],
):
    return await ExamService(db).update(user, _school(user), exam_id, body)


@router.delete("/{exam_id}", status_code=204)
async def delete_exam(
    exam_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("exams.write"))],
):
    await ExamService(db).delete(user, _school(user), exam_id)
