from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.announcements.schemas import AnnouncementCreate, AnnouncementOut, AnnouncementUpdate
from src.modules.announcements.service import AnnouncementService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/announcements", tags=["announcements"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[AnnouncementOut])
async def list_announcements(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("announcements.read"))],
    page: PageQuery,
):
    items, total = await AnnouncementService(db).list(user, _school(user), page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    body: AnnouncementCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("announcements.write"))],
):
    return await AnnouncementService(db).create(user, _school(user), body)


@router.get("/{announcement_id}", response_model=AnnouncementOut)
async def get_announcement(
    announcement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("announcements.read"))],
):
    return await AnnouncementService(db).get(user, _school(user), announcement_id)


@router.patch("/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: UUID,
    body: AnnouncementUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("announcements.write"))],
):
    return await AnnouncementService(db).update(user, _school(user), announcement_id, body)
