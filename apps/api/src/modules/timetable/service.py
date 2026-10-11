from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError, ValidationError
from src.db.models import TimetableEntry
from src.modules.access.scope import restrict_classes
from src.modules.timetable.schemas import TimetableEntryCreate
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


def _check_times(starts, ends) -> None:
    if ends <= starts:
        raise ValidationError("ends_at must be after starts_at")


class TimetableService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TimetableEntryCreate) -> TimetableEntry:
        _check_times(data.starts_at, data.ends_at)
        row = TimetableEntry(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(
        self, user: CurrentUser, school_id: UUID, page: PageParams, class_id: UUID | None = None
    ) -> tuple[list[TimetableEntry], int]:
        stmt = select(TimetableEntry).where(TimetableEntry.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, TimetableEntry.class_id)
        if class_id:
            stmt = stmt.where(TimetableEntry.class_id == class_id)
        stmt = stmt.order_by(TimetableEntry.day_of_week, TimetableEntry.starts_at)
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, entry_id: UUID) -> TimetableEntry:
        stmt = select(TimetableEntry).where(TimetableEntry.id == entry_id, TimetableEntry.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, TimetableEntry.class_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Timetable entry not found")
        return row

    async def update(self, user: CurrentUser, school_id: UUID, entry_id: UUID, data) -> TimetableEntry:
        row = await self.get(user, school_id, entry_id)
        payload = data.model_dump(exclude_unset=True)
        starts = payload.get("starts_at", row.starts_at)
        ends = payload.get("ends_at", row.ends_at)
        _check_times(starts, ends)
        for key, value in payload.items():
            setattr(row, key, value)
        await self.db.flush()
        return row

    async def delete(self, user: CurrentUser, school_id: UUID, entry_id: UUID) -> None:
        row = await self.get(user, school_id, entry_id)
        await self.db.delete(row)
        await self.db.flush()
