from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Message
from src.modules.access.scope import same_school_user
from src.modules.messaging.schemas import MessageCreate
from src.shared.enums import UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


class MessagingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def send(self, user: CurrentUser, school_id: UUID, data: MessageCreate) -> Message:
        if data.recipient_id == user.id:
            raise ForbiddenError("Choose a different recipient")
        if not await same_school_user(self.db, data.recipient_id):
            raise NotFoundError("Recipient not found")
        if user.role in {UserRole.STUDENT, UserRole.PARENT, UserRole.TEACHER}:
            from src.modules.communication.service import CommunicationService

            await CommunicationService(self.db)._assert_can_message(
                school_id, user.id, user.role.value, data.recipient_id, None
            )
        row = Message(
            school_id=school_id,
            sender_id=user.id,
            recipient_id=data.recipient_id,
            subject=data.subject,
            body=data.body,
            is_read=False,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, user: CurrentUser, school_id: UUID, page: PageParams, box: str) -> tuple[list[Message], int]:
        stmt = select(Message).where(Message.school_id == school_id)
        if user.role not in {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN} or box != "all":
            if box == "sent":
                stmt = stmt.where(Message.sender_id == user.id)
            elif box == "all":
                stmt = stmt.where(or_(Message.sender_id == user.id, Message.recipient_id == user.id))
            else:
                stmt = stmt.where(Message.recipient_id == user.id)
        stmt = stmt.order_by(Message.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, message_id: UUID) -> Message:
        stmt = select(Message).where(Message.id == message_id, Message.school_id == school_id)
        if user.role not in {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN}:
            stmt = stmt.where(or_(Message.sender_id == user.id, Message.recipient_id == user.id))
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Message not found")
        return row

    async def mark_read(self, user: CurrentUser, school_id: UUID, message_id: UUID) -> Message:
        row = await self.get(user, school_id, message_id)
        if row.recipient_id != user.id and user.role not in {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN}:
            raise ForbiddenError("Only the recipient can mark a message read")
        row.is_read = True
        await self.db.flush()
        return row
