from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TimetableEntry
from src.modules.timetable.schemas import TimetableEntryCreate


class TimetableService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TimetableEntryCreate) -> TimetableEntry:
        row = TimetableEntry(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID, class_id: UUID | None = None) -> list[TimetableEntry]:
        q = select(TimetableEntry).where(TimetableEntry.school_id == school_id)
        if class_id:
            q = q.where(TimetableEntry.class_id == class_id)
        result = await self.db.execute(q.order_by(TimetableEntry.day_of_week, TimetableEntry.starts_at))
        return list(result.scalars().all())
