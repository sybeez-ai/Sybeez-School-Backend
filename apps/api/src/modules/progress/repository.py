"""Persistence for student progress — always filter by school_id for tenant isolation."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import PracticeAttempt, StudentProgress


class ProgressRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_student(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        academic_year_id: UUID | None = None,
    ) -> list[StudentProgress]:
        stmt = select(StudentProgress).where(
            StudentProgress.school_id == school_id,
            StudentProgress.student_id == student_id,
        )
        if academic_year_id is not None:
            stmt = stmt.where(StudentProgress.academic_year_id == academic_year_id)
        return list((await self.db.execute(stmt)).scalars().all())

    async def upsert(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        subject_id: UUID,
        academic_year_id: UUID | None,
        metric: str,
        value: float,
    ) -> StudentProgress:
        """Insert or update a StudentProgress row, return the row."""
        stmt = select(StudentProgress).where(
            StudentProgress.school_id == school_id,
            StudentProgress.student_id == student_id,
            StudentProgress.subject_id == subject_id,
            StudentProgress.metric == metric,
        )
        if academic_year_id is not None:
            stmt = stmt.where(StudentProgress.academic_year_id == academic_year_id)
        else:
            stmt = stmt.where(StudentProgress.academic_year_id.is_(None))

        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row:
            row.value = value
        else:
            row = StudentProgress(
                school_id=school_id,
                student_id=student_id,
                subject_id=subject_id,
                academic_year_id=academic_year_id,
                metric=metric,
                value=value,
            )
            self.db.add(row)
            await self.db.flush()
        return row

    async def create_practice_attempt(
        self,
        *,
        school_id: UUID | None,
        student_user_id: UUID,
        subject_id: UUID | None,
        topic: str | None,
        difficulty: str,
        questions_json: str,
        answers_json: str,
        score_pct: float | None,
    ) -> PracticeAttempt:
        row = PracticeAttempt(
            school_id=school_id,
            student_user_id=student_user_id,
            subject_id=subject_id,
            topic=topic,
            difficulty=difficulty,
            questions_json=questions_json,
            answers_json=answers_json,
            score_pct=score_pct,
            completed_at=datetime.now(timezone.utc),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def list_practice_attempts(
        self,
        *,
        school_id: UUID | None,
        student_user_id: UUID,
        limit: int = 10,
    ) -> list[PracticeAttempt]:
        stmt = (
            select(PracticeAttempt)
            .where(PracticeAttempt.student_user_id == student_user_id)
            .order_by(PracticeAttempt.created_at.desc())
            .limit(limit)
        )
        if school_id is not None:
            stmt = stmt.where(PracticeAttempt.school_id == school_id)
        return list((await self.db.execute(stmt)).scalars().all())
