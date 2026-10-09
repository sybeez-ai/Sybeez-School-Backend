from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.db.models import Notification
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import UserRole

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: UUID
    title: str
    body: str
    category: str | None = None
    link_path: str | None = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/health")
async def notifications_health():
    return {"module": "notifications", "status": "ok"}


@router.get("", response_model=list[NotificationOut])
async def list_my_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_school_scope)],
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.is_read.is_(False))
    result = await db.execute(q.order_by(Notification.created_at.desc()).limit(limit))
    return list(result.scalars().all())


@router.post("/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.PARENT, UserRole.TEACHER, UserRole.SCHOOL_ADMIN))],
):
    row = await db.get(Notification, notification_id)
    if not row or row.user_id != user.id:
        raise ForbiddenError("Notification not found")
    row.is_read = True
    row.read_at = datetime.utcnow()
    return row
