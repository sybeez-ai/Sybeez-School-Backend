from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Teacher, TeachingRecord
from src.modules.teaching_records.repository import TeachingRecordRepository
from src.modules.teaching_records.schemas import TeachingRecordCreate


class TeachingRecordService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = TeachingRecordRepository(db)

    async def _teacher_for_user(self, school_id: UUID, user_id: UUID) -> Teacher:
        result = await self.db.execute(
            select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
        )
        teacher = result.scalar_one_or_none()
        if teacher is None:
            raise ForbiddenError("Teacher profile required")
        return teacher

    async def create(self, *, school_id: UUID, user_id: UUID, data: TeachingRecordCreate) -> TeachingRecord:
        teacher = await self._teacher_for_user(school_id, user_id)
        record = TeachingRecord(
            school_id=school_id,
            teacher_id=teacher.id,
            class_id=data.class_id,
            subject_id=data.subject_id,
            taught_on=data.taught_on,
            topic=data.topic,
            summary=data.summary,
            objectives=data.objectives,
            homework_notes=data.homework_notes,
        )
        return await self.repo.create(record)

    async def list_for_class(self, *, school_id: UUID, class_id: UUID) -> list[TeachingRecord]:
        return await self.repo.list_for_class(school_id=school_id, class_id=class_id)

    async def list_mine(self, *, school_id: UUID) -> list[TeachingRecord]:
        return await self.repo.list_for_school(school_id=school_id)
