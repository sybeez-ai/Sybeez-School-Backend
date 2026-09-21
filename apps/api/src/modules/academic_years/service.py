from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import AcademicYear
from src.modules.academic_years.schemas import AcademicYearCreate


class AcademicYearService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: AcademicYearCreate) -> AcademicYear:
        if data.is_current:
            await self.db.execute(
                update(AcademicYear).where(AcademicYear.school_id == school_id).values(is_current=False)
            )
        row = AcademicYear(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID) -> list[AcademicYear]:
        result = await self.db.execute(
            select(AcademicYear).where(AcademicYear.school_id == school_id).order_by(AcademicYear.starts_on.desc())
        )
        return list(result.scalars().all())

    async def get(self, school_id: UUID, year_id: UUID) -> AcademicYear:
        result = await self.db.execute(
            select(AcademicYear).where(AcademicYear.id == year_id, AcademicYear.school_id == school_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            raise NotFoundError("Academic year not found")
        return row
