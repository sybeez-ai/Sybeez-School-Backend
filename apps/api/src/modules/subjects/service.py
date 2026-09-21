from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Subject
from src.modules.subjects.schemas import SubjectCreate


class SubjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: SubjectCreate) -> Subject:
        row = Subject(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID) -> list[Subject]:
        result = await self.db.execute(
            select(Subject).where(Subject.school_id == school_id).order_by(Subject.name)
        )
        return list(result.scalars().all())
