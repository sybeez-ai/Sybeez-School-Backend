from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ClassRoom
from src.modules.classes.schemas import ClassCreate


class ClassService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: ClassCreate) -> ClassRoom:
        row = ClassRoom(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID) -> list[ClassRoom]:
        result = await self.db.execute(
            select(ClassRoom).where(ClassRoom.school_id == school_id).order_by(ClassRoom.name)
        )
        return list(result.scalars().all())
