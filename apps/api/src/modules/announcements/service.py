from __future__ import annotations

from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError
from src.db.models import Announcement
from src.modules.access.scope import restrict_classes
from src.modules.announcements.schemas import AnnouncementCreate, AnnouncementUpdate
from src.shared.enums import UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams

_SCHOOL_WIDE = {UserRole.PLATFORM_ADMIN, UserRole.SCHOOL_ADMIN, UserRole.FINANCE, UserRole.HR, UserRole.PAYROLL_APPROVER}


class AnnouncementService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, user: CurrentUser, school_id: UUID, data: AnnouncementCreate) -> Announcement:
        row = Announcement(
            school_id=school_id,
            author_id=user.id,
            class_id=data.class_id,
            title=data.title,
            body=data.body,
            is_published=data.is_published,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    def _visible(self, stmt, user: CurrentUser, school_id: UUID):
        if user.role in {UserRole.STUDENT, UserRole.PARENT, UserRole.FINANCE, UserRole.HR, UserRole.PAYROLL_APPROVER}:
            stmt = stmt.where(Announcement.is_published.is_(True))
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
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self.db.flush()
        return row
