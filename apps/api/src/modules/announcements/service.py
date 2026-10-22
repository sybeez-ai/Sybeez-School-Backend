from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Announcement
from src.modules.access.scope import assert_teacher_class, restrict_classes
from src.modules.announcements.schemas import AnnouncementCreate, AnnouncementUpdate
from src.shared.enums import AnnouncementStatus, UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams

_SCHOOL_WIDE = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE, UserRole.HR, UserRole.PAYROLL_APPROVER}


class AnnouncementService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user: CurrentUser, school_id: UUID, data: AnnouncementCreate) -> Announcement:
        if user.role == UserRole.TEACHER:
            if data.class_id is None:
                raise ForbiddenError("Teachers can only announce to their classes")
            await assert_teacher_class(self.db, user.id, school_id, data.class_id)
        published = bool(data.is_published)
        row = Announcement(
            school_id=school_id,
            author_id=user.id,
            class_id=data.class_id,
            title=data.title,
            body=data.body,
            is_published=published,
            status=AnnouncementStatus.PUBLISHED.value if published else AnnouncementStatus.DRAFT.value,
            published_at=datetime.now(timezone.utc) if published else None,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    def _visible(self, stmt, user: CurrentUser, school_id: UUID):
        if user.role in {
            UserRole.STUDENT,
            UserRole.PARENT,
            UserRole.FINANCE,
            UserRole.HR,
            UserRole.PAYROLL_APPROVER,
            UserRole.TEACHER,
        }:
            stmt = stmt.where(
                or_(Announcement.is_published.is_(True), Announcement.author_id == user.id)
            )
        if user.role in _SCHOOL_WIDE:
            if user.role != UserRole.SCHOOL_ADMIN and user.role != UserRole.PLATFORM_ADMIN:
                stmt = stmt.where(Announcement.class_id.is_(None))
            return stmt
        class_scope = restrict_classes(select(Announcement.id), user, school_id, Announcement.class_id)
        # School-wide posts (no class) plus class-scoped posts the actor can see.
        return stmt.where(or_(Announcement.class_id.is_(None), Announcement.id.in_(class_scope)))

    async def list(self, user: CurrentUser, school_id: UUID, page: PageParams) -> tuple[list[Announcement], int]:
        stmt = select(Announcement).where(Announcement.school_id == school_id)
        stmt = self._visible(stmt, user, school_id)
        stmt = stmt.order_by(Announcement.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, announcement_id: UUID) -> Announcement:
        stmt = select(Announcement).where(Announcement.id == announcement_id, Announcement.school_id == school_id)
        stmt = self._visible(stmt, user, school_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Announcement not found")
        return row

    async def update(
        self, user: CurrentUser, school_id: UUID, announcement_id: UUID, data: AnnouncementUpdate
    ) -> Announcement:
        # Authors and admins can load drafts. Re-read without the published filter when writing.
        stmt = select(Announcement).where(Announcement.id == announcement_id, Announcement.school_id == school_id)
        if user.role not in {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN}:
            stmt = stmt.where(Announcement.author_id == user.id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Announcement not found")
        payload = data.model_dump(exclude_unset=True)
        if user.role == UserRole.TEACHER:
            next_class = payload.get("class_id", row.class_id)
            if next_class is None:
                raise ForbiddenError("Teachers can only announce to their classes")
            await assert_teacher_class(self.db, user.id, school_id, next_class)
        was_published = bool(row.is_published)
        if "is_published" in payload:
            published = bool(payload["is_published"])
            row.status = AnnouncementStatus.PUBLISHED.value if published else AnnouncementStatus.DRAFT.value
            row.published_at = datetime.now(timezone.utc) if published else None
        for key, value in payload.items():
            setattr(row, key, value)
        await self.db.flush()
        row.just_published = bool(row.is_published) and not was_published  # transient, not a column
        # Keep cached translations' visibility flag in step; changed text is detected by hash.
        from src.modules.parent_messages.service import ParentMessageService

        await ParentMessageService(self.db).sync_published(school_id, "announcement", row.id, bool(row.is_published))
        return row
