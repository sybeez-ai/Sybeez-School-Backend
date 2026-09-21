from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TeacherAssignment
from src.modules.teacher_assignments.schemas import TeacherAssignmentCreate


class TeacherAssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TeacherAssignmentCreate) -> TeacherAssignment:
        row = TeacherAssignment(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID) -> list[TeacherAssignment]:
        result = await self.db.execute(
            select(TeacherAssignment)
            .where(TeacherAssignment.school_id == school_id)
            .order_by(TeacherAssignment.created_at.desc())
        )
        return list(result.scalars().all())
