from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Grade
from src.modules.grades.schemas import GradeCreate, GradeUpdate


class GradeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: GradeCreate) -> Grade:
        existing = (
            await self.db.execute(
                select(Grade).where(Grade.school_id == school_id, Grade.name == data.name.strip())
            )
        ).scalar_one_or_none()
        if existing:
            # Re-enable / update if admin re-adds a known level
            existing.level = data.level
            existing.is_active = data.is_active
            await self.db.flush()
            return existing
        row = Grade(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID, *, active_only: bool = False) -> list[Grade]:
        q = select(Grade).where(Grade.school_id == school_id)
        if active_only:
            q = q.where(Grade.is_active.is_(True))
        result = await self.db.execute(q.order_by(Grade.level, Grade.name))
        return list(result.scalars().all())

    async def get(self, school_id: UUID, grade_id: UUID) -> Grade:
        row = (
            await self.db.execute(
                select(Grade).where(Grade.id == grade_id, Grade.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError("Grade not found")
        return row

    async def update(self, school_id: UUID, grade_id: UUID, data: GradeUpdate) -> Grade:
        row = await self.get(school_id, grade_id)
        payload = data.model_dump(exclude_unset=True)
        if "name" in payload and payload["name"]:
            name = payload["name"].strip()
            clash = (
                await self.db.execute(
                    select(Grade).where(
                        Grade.school_id == school_id,
                        Grade.name == name,
                        Grade.id != grade_id,
                    )
                )
            ).scalar_one_or_none()
            if clash:
                raise ConflictError(f"Grade '{name}' already exists")
            payload["name"] = name
        for k, v in payload.items():
            setattr(row, k, v)
        await self.db.flush()
        return row
