from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.homework.schemas import (
    HomeworkCreate,
    HomeworkOut,
    HomeworkUpdate,
    SubmissionCreate,
    SubmissionGrade,
    SubmissionOut,
)
from src.modules.homework.service import HomeworkService
from src.shared.pagination import Page, PageQuery


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


def build_work_router(*, prefix: str, tag: str, read: str, write: str, submit: str, grade: str) -> APIRouter:
    """Homework and assignments share one table and one service. Permissions differ by portal name."""
    router = APIRouter(prefix=prefix, tags=[tag])

    @router.get("", response_model=Page[HomeworkOut])
    async def list_items(
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(read))],
        page: PageQuery,
        class_id: UUID | None = None,
    ):
        items, total = await HomeworkService(db).list(user, _school(user), page, class_id)
        return Page(items=items, total=total, page=page.page, page_size=page.page_size)

    @router.post("", response_model=HomeworkOut, status_code=201)
    async def create_item(
        body: HomeworkCreate,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(write))],
    ):
        return await HomeworkService(db).create(_school(user), body)

    @router.get("/{item_id}", response_model=HomeworkOut)
    async def get_item(
        item_id: UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(read))],
    ):
        return await HomeworkService(db).get(user, _school(user), item_id)

    @router.patch("/{item_id}", response_model=HomeworkOut)
    async def update_item(
        item_id: UUID,
        body: HomeworkUpdate,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(write))],
    ):
        return await HomeworkService(db).update(user, _school(user), item_id, body)

    @router.post("/{item_id}/submissions", response_model=SubmissionOut, status_code=201)
    async def submit_item(
        item_id: UUID,
        body: SubmissionCreate,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(submit))],
    ):
        return await HomeworkService(db).submit(user, _school(user), item_id, body)

    @router.get("/{item_id}/submissions", response_model=Page[SubmissionOut])
    async def list_submissions(
        item_id: UUID,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(read))],
        page: PageQuery,
    ):
        items, total = await HomeworkService(db).list_submissions(user, _school(user), item_id, page)
        return Page(items=items, total=total, page=page.page, page_size=page.page_size)

    @router.patch("/{item_id}/submissions/{submission_id}", response_model=SubmissionOut)
    async def grade_submission(
        item_id: UUID,
        submission_id: UUID,
        body: SubmissionGrade,
        db: Annotated[AsyncSession, Depends(get_db)],
        user: Annotated[CurrentUser, Depends(require_permission(grade))],
    ):
        return await HomeworkService(db).grade(user, _school(user), item_id, submission_id, body)

    return router


router = build_work_router(
    prefix="/homework",
    tag="homework",
    read="homework.read",
    write="homework.write",
    submit="homework.submit",
    grade="homework.grade",
)
