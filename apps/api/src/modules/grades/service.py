from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Grade
from src.modules.grades.schemas import GradeCreate


class GradeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: GradeCreate) -> Grade:
        row = Grade(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID) -> list[Grade]:
        result = await self.db.execute(
            select(Grade).where(Grade.school_id == school_id).order_by(Grade.level, Grade.name)
        )
        return list(result.scalars().all())
