from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.results.schemas import ResultCreate, ResultOut, ResultUpdate
from src.modules.results.service import ResultService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/results", tags=["results"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[ResultOut])
async def list_results(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("results.read"))],
    page: PageQuery,
    exam_id: UUID | None = None,
    student_id: UUID | None = None,
):
    items, total = await ResultService(db).list(user, _school(user), page, exam_id=exam_id, student_id=student_id)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=ResultOut, status_code=201)
async def create_result(
    body: ResultCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("results.write"))],
):
    return await ResultService(db).create(user, _school(user), body)


@router.get("/{result_id}", response_model=ResultOut)
async def get_result(
    result_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("results.read"))],
):
    return await ResultService(db).get(user, _school(user), result_id)


@router.patch("/{result_id}", response_model=ResultOut)
async def update_result(
    result_id: UUID,
    body: ResultUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("results.write"))],
):
    return await ResultService(db).update(user, _school(user), result_id, body)
