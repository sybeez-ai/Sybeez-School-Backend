from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, ValidationError
from src.modules.access.deps import require_permission
from src.modules.messaging.schemas import MessageCreate, MessageOut
from src.modules.messaging.service import MessagingService
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/messaging", tags=["messaging"])


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=Page[MessageOut])
async def list_messages(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("messages.read"))],
    page: PageQuery,
    box: str = Query("inbox"),
):
    if box not in {"inbox", "sent", "all"}:
        raise ValidationError("box must be inbox, sent, or all")
    items, total = await MessagingService(db).list(user, _school(user), page, box)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.post("", response_model=MessageOut, status_code=201)
async def send_message(
    body: MessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("messages.write"))],
):
    return await MessagingService(db).send(user, _school(user), body)


@router.get("/{message_id}", response_model=MessageOut)
async def get_message(
    message_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("messages.read"))],
):
    return await MessagingService(db).get(user, _school(user), message_id)


@router.post("/{message_id}/read", response_model=MessageOut)
async def mark_read(
    message_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("messages.read"))],
):
    return await MessagingService(db).mark_read(user, _school(user), message_id)
