"""Persistence for homework — always filter by school_id."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import (
    Enrollment,
    Homework,
    HomeworkAttachment,
    HomeworkSubmission,
    SubmissionAttachment,
)
from src.shared.enums import EnrollmentStatus, HomeworkStatus


class HomeworkRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get(self, school_id: UUID, homework_id: UUID) -> Homework | None:
        result = await self.db.execute(
            select(Homework)
            .options(
                selectinload(Homework.attachments),
                selectinload(Homework.submissions).selectinload(HomeworkSubmission.attachments),
            )
            .where(Homework.school_id == school_id, Homework.id == homework_id)
        )
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        school_id: UUID,
        class_id: UUID | None = None,
        subject_id: UUID | None = None,
        teacher_id: UUID | None = None,
        academic_year_id: UUID | None = None,
        status: str | None = None,
        homework_type: str | None = None,
        homework_types: list[str] | None = None,
        due_from: datetime | None = None,
        due_to: datetime | None = None,
        limit: int = 100,
    ) -> list[Homework]:
        q = (
            select(Homework)
            .options(selectinload(Homework.attachments))
            .where(Homework.school_id == school_id)
        )
        if class_id:
            q = q.where(Homework.class_id == class_id)
        if subject_id:
            q = q.where(Homework.subject_id == subject_id)
        if teacher_id:
            q = q.where(Homework.teacher_id == teacher_id)
        if academic_year_id:
            q = q.where(Homework.academic_year_id == academic_year_id)
        if status:
            q = q.where(Homework.status == status)
        if homework_types:
            q = q.where(Homework.homework_type.in_(homework_types))
        elif homework_type:
            q = q.where(Homework.homework_type == homework_type)
        if due_from:
            q = q.where(Homework.due_at >= due_from)
        if due_to:
            q = q.where(Homework.due_at <= due_to)
        result = await self.db.execute(q.order_by(Homework.due_at.asc().nullslast(), Homework.created_at.desc()).limit(limit))
        return list(result.scalars().all())

    async def list_for_section(
        self,
        *,
        school_id: UUID,
        class_id: UUID,
        published_only: bool = True,
        homework_type: str | None = None,
        homework_types: list[str] | None = None,
    ) -> list[Homework]:
        q = (
            select(Homework)
            .options(selectinload(Homework.attachments))
            .where(Homework.school_id == school_id, Homework.class_id == class_id)
        )
        if published_only:
            q = q.where(Homework.status.in_([HomeworkStatus.PUBLISHED.value, HomeworkStatus.CLOSED.value]))
        if homework_types:
            q = q.where(Homework.homework_type.in_(homework_types))
        elif homework_type:
            q = q.where(Homework.homework_type == homework_type)
        result = await self.db.execute(q.order_by(Homework.due_at.asc().nullslast()))
        return list(result.scalars().all())

    async def create(self, row: Homework) -> Homework:
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete(self, row: Homework) -> None:
        await self.db.delete(row)

    async def count_enrolled(self, school_id: UUID, class_id: UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.school_id == school_id,
                Enrollment.class_id == class_id,
                Enrollment.status == EnrollmentStatus.ACTIVE.value,
            )
        )
        return int(result.scalar_one() or 0)

    async def get_submission(
        self, *, school_id: UUID, homework_id: UUID, student_id: UUID
    ) -> HomeworkSubmission | None:
        result = await self.db.execute(
            select(HomeworkSubmission)
            .options(selectinload(HomeworkSubmission.attachments))
            .where(
                HomeworkSubmission.school_id == school_id,
                HomeworkSubmission.homework_id == homework_id,
                HomeworkSubmission.student_id == student_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_submission_by_id(
        self, *, school_id: UUID, submission_id: UUID
    ) -> HomeworkSubmission | None:
        result = await self.db.execute(
            select(HomeworkSubmission)
            .options(selectinload(HomeworkSubmission.attachments))
            .where(
                HomeworkSubmission.school_id == school_id,
                HomeworkSubmission.id == submission_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_submissions(
        self, *, school_id: UUID, homework_id: UUID
    ) -> list[HomeworkSubmission]:
        result = await self.db.execute(
            select(HomeworkSubmission)
            .options(selectinload(HomeworkSubmission.attachments))
            .where(
                HomeworkSubmission.school_id == school_id,
                HomeworkSubmission.homework_id == homework_id,
            )
            .order_by(HomeworkSubmission.submitted_at.desc().nullslast())
        )
        return list(result.scalars().all())

    async def add_homework_attachment(self, row: HomeworkAttachment) -> HomeworkAttachment:
        self.db.add(row)
        await self.db.flush()
        return row

    async def add_submission_attachment(self, row: SubmissionAttachment) -> SubmissionAttachment:
        self.db.add(row)
        await self.db.flush()
        return row
