from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import Homework, HomeworkSubmission, Student
from src.modules.access.scope import restrict_classes, restrict_student_rows
from src.modules.homework.schemas import HomeworkCreate, HomeworkUpdate, SubmissionCreate, SubmissionGrade
from src.shared.enums import HomeworkStatus, HomeworkSubmissionStatus, UserRole
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams
from src.shared.utils import utcnow


class HomeworkService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: HomeworkCreate) -> Homework:
        payload = data.model_dump()
        payload["status"] = data.status.value
        row = Homework(school_id=school_id, **payload)
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, user: CurrentUser, school_id: UUID, page: PageParams, class_id: UUID | None) -> tuple[list[Homework], int]:
        stmt = select(Homework).where(Homework.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Homework.class_id)
        if user.role in {UserRole.STUDENT, UserRole.PARENT}:
            stmt = stmt.where(Homework.status != HomeworkStatus.DRAFT.value)
        if class_id:
            stmt = stmt.where(Homework.class_id == class_id)
        stmt = stmt.order_by(Homework.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, homework_id: UUID) -> Homework:
        stmt = select(Homework).where(Homework.id == homework_id, Homework.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Homework.class_id)
        if user.role in {UserRole.STUDENT, UserRole.PARENT}:
            stmt = stmt.where(Homework.status != HomeworkStatus.DRAFT.value)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Homework not found")
        return row

    async def update(self, user: CurrentUser, school_id: UUID, homework_id: UUID, data: HomeworkUpdate) -> Homework:
        row = await self.get(user, school_id, homework_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if key == "status" and value is not None:
                value = value.value
            setattr(row, key, value)
        await self.db.flush()
        return row

    async def _own_student(self, user: CurrentUser, school_id: UUID) -> Student:
        row = (
            await self.db.execute(select(Student).where(Student.user_id == user.id, Student.school_id == school_id))
        ).scalar_one_or_none()
        if row is None:
            raise ForbiddenError("No student profile for this account")
        return row

    async def submit(self, user: CurrentUser, school_id: UUID, homework_id: UUID, data: SubmissionCreate) -> HomeworkSubmission:
        homework = await self.get(user, school_id, homework_id)
        if homework.status == HomeworkStatus.CLOSED.value:
            raise ConflictError("Homework is closed")
        student = await self._own_student(user, school_id)
        status = HomeworkSubmissionStatus.SUBMITTED.value
        if homework.due_at is not None and utcnow() > homework.due_at:
            status = HomeworkSubmissionStatus.LATE.value
        row = HomeworkSubmission(
            homework_id=homework.id,
            student_id=student.id,
            school_id=school_id,
            content=data.content,
            status=status,
            submitted_at=utcnow(),
        )
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Homework already submitted") from exc
        return row

    async def list_submissions(
        self, user: CurrentUser, school_id: UUID, homework_id: UUID, page: PageParams
    ) -> tuple[list[HomeworkSubmission], int]:
        await self.get(user, school_id, homework_id)
        stmt = select(HomeworkSubmission).where(
            HomeworkSubmission.homework_id == homework_id, HomeworkSubmission.school_id == school_id
        )
        stmt = restrict_student_rows(stmt, user, school_id, HomeworkSubmission.student_id)
        stmt = stmt.order_by(HomeworkSubmission.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def grade(
        self, user: CurrentUser, school_id: UUID, homework_id: UUID, submission_id: UUID, data: SubmissionGrade
    ) -> HomeworkSubmission:
        await self.get(user, school_id, homework_id)
        stmt = select(HomeworkSubmission).where(
            HomeworkSubmission.id == submission_id,
            HomeworkSubmission.homework_id == homework_id,
            HomeworkSubmission.school_id == school_id,
        )
        stmt = restrict_student_rows(stmt, user, school_id, HomeworkSubmission.student_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Submission not found")
        row.marks = data.marks
        row.feedback = data.feedback
        row.status = HomeworkSubmissionStatus.GRADED.value
        await self.db.flush()
        return row
