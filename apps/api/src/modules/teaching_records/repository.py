from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TeachingRecord


class TeachingRecordRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, record: TeachingRecord) -> TeachingRecord:
        self.db.add(record)
        await self.db.flush()
        return record

    async def list_for_class(
        self, *, school_id: UUID, class_id: UUID, limit: int = 50
    ) -> list[TeachingRecord]:
        result = await self.db.execute(
            select(TeachingRecord)
            .where(TeachingRecord.school_id == school_id, TeachingRecord.class_id == class_id)
            .order_by(TeachingRecord.taught_on.desc(), TeachingRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_for_school(self, *, school_id: UUID, limit: int = 50) -> list[TeachingRecord]:
        result = await self.db.execute(
            select(TeachingRecord)
            .where(TeachingRecord.school_id == school_id)
            .order_by(TeachingRecord.taught_on.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
