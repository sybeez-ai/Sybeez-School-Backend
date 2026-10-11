from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.timetable.schemas import TimetableEntryCreate, TimetableEntryOut, TimetableEntryUpdate
from src.modules.timetable.service import TimetableService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/timetable", tags=["timetable"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[TimetableEntryOut])
async def list_timetable(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.read"))],
    page: PageQuery,
    class_id: UUID | None = None,
):
    items, total = await TimetableService(db).list(user, _sid(user), page, class_id)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=TimetableEntryOut, status_code=201)
async def create_entry(
    body: TimetableEntryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.write"))],
):
    return await TimetableService(db).create(_sid(user), body)


@router.get("/{entry_id}", response_model=TimetableEntryOut)
async def get_entry(
    entry_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.read"))],
):
    return await TimetableService(db).get(user, _sid(user), entry_id)


@router.patch("/{entry_id}", response_model=TimetableEntryOut)
async def update_entry(
    entry_id: UUID,
    body: TimetableEntryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.write"))],
):
    return await TimetableService(db).update(user, _sid(user), entry_id, body)


@router.delete("/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("timetable.write"))],
):
    await TimetableService(db).delete(user, _sid(user), entry_id)
