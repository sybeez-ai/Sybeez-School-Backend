"""AI teacher sessions recorded on the school side. Chat itself stays on the AI service."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Enrollment, LearningSession, Student, Subject, TeachingRecord
from src.modules.access.scope import restrict_student_rows
from src.modules.learning_sessions.schemas import LearningSessionOut, LearningSessionStart
from src.shared.enums import EnrollmentStatus, UserRole


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LearningSessionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _student(self, school_id: UUID, user_id: UUID) -> Student:
        student = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise ForbiddenError("Student profile required")
        enrolled = (
            await self.db.execute(
                select(Enrollment.id).where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == student.id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                ).limit(1)
            )
        ).scalar_one_or_none()
        if enrolled is None:
            raise ForbiddenError("No active enrollment")
        return student

    async def _out(self, row: LearningSession) -> LearningSessionOut:
        return LearningSessionOut.model_validate(row)

    async def list_visible(self, user: CurrentUser, school_id: UUID) -> list[LearningSessionOut]:
        stmt = (
            select(LearningSession)
            .where(LearningSession.school_id == school_id)
            .order_by(LearningSession.started_at.desc())
        )
        stmt = restrict_student_rows(stmt, user, school_id, LearningSession.student_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [await self._out(row) for row in rows]

    async def start(self, user: CurrentUser, school_id: UUID, data: LearningSessionStart) -> LearningSessionOut:
        if user.role != UserRole.STUDENT:
            raise ForbiddenError("Only a student can start an AI teacher session")
        student = await self._student(school_id, user.id)
        if data.subject_id is not None:
            subject = await self.db.get(Subject, data.subject_id)
            if subject is None or subject.school_id != school_id:
                raise NotFoundError("Subject not found")
        if data.teaching_record_id is not None:
            record = await self.db.get(TeachingRecord, data.teaching_record_id)
            if record is None or record.school_id != school_id:
                raise NotFoundError("Teaching record not found")
        now = _utcnow()
        open_rows = (
            await self.db.execute(
                select(LearningSession).where(
                    LearningSession.school_id == school_id,
                    LearningSession.student_id == student.id,
                    LearningSession.ended_at.is_(None),
                )
            )
        ).scalars().all()
        for row in open_rows:
            row.ended_at = now
            started = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=timezone.utc)
            row.duration_seconds = max(0, int((now - started).total_seconds()))
        created = LearningSession(
            school_id=school_id,
            student_id=student.id,
            subject_id=data.subject_id,
            teaching_record_id=data.teaching_record_id,
            mode=data.mode,
            started_at=now,
            duration_seconds=0,
        )
        self.db.add(created)
        await self.db.flush()
        return await self._out(created)

    async def end(self, user: CurrentUser, school_id: UUID, session_id: UUID) -> LearningSessionOut:
        if user.role != UserRole.STUDENT:
            raise ForbiddenError("Only a student can end an AI teacher session")
        student = await self._student(school_id, user.id)
        row = await self.db.get(LearningSession, session_id)
        if row is None or row.school_id != school_id or row.student_id != student.id:
            raise NotFoundError("Learning session not found")
        if row.ended_at is None:
            now = _utcnow()
            row.ended_at = now
            started = row.started_at if row.started_at.tzinfo else row.started_at.replace(tzinfo=timezone.utc)
            row.duration_seconds = max(0, int((now - started).total_seconds()))
            await self.db.flush()
        return await self._out(row)
