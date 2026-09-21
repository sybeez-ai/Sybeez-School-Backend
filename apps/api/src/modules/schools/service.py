from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import School


class SchoolsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_my_school(self, school_id: UUID | None) -> School:
        if school_id is None:
            raise ForbiddenError("School context required")
        result = await self.db.execute(select(School).where(School.id == school_id))
        school = result.scalar_one_or_none()
        if school is None:
            raise NotFoundError("School not found")
        return school
