from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, NotFoundError, ValidationError
from src.db.models import Exam, Result
from src.modules.access.scope import restrict_student_rows
from src.modules.exams.service import ExamService
from src.modules.results.schemas import ResultCreate, ResultUpdate
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


def grade_label(percentage: float) -> str:
    if percentage >= 90:
        return "A"
    if percentage >= 80:
        return "B"
    if percentage >= 70:
        return "C"
    if percentage >= 60:
        return "D"
    return "F"


class ResultService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _apply_score(self, row: Result, exam: Exam, score: float) -> None:
        if score > exam.max_score:
            raise ValidationError("Score cannot exceed the exam maximum")
        row.score = score
        row.percentage = (score / exam.max_score) * 100 if exam.max_score else 0
        row.grade_label = grade_label(row.percentage)

    async def create(self, user: CurrentUser, school_id: UUID, data: ResultCreate) -> Result:
        exam = (
            await self.db.execute(select(Exam).where(Exam.id == data.exam_id, Exam.school_id == school_id))
        ).scalar_one_or_none()
        if exam is None:
            raise NotFoundError("Exam not found")
        row = Result(school_id=school_id, exam_id=exam.id, student_id=data.student_id, remarks=data.remarks, score=0)
        self._apply_score(row, exam, data.score)
        self.db.add(row)
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("A result already exists for this student and exam") from exc
        return row

    async def list(
        self,
        user: CurrentUser,
        school_id: UUID,
        page: PageParams,
        *,
        exam_id: UUID | None,
        student_id: UUID | None,
    ) -> tuple[list[Result], int]:
        stmt = select(Result).where(Result.school_id == school_id)
        stmt = restrict_student_rows(stmt, user, school_id, Result.student_id)
        if exam_id:
            stmt = stmt.where(Result.exam_id == exam_id)
        if student_id:
            stmt = stmt.where(Result.student_id == student_id)
        stmt = stmt.order_by(Result.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, result_id: UUID) -> Result:
        stmt = select(Result).where(Result.id == result_id, Result.school_id == school_id)
        stmt = restrict_student_rows(stmt, user, school_id, Result.student_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Result not found")
        return row

    async def update(self, user: CurrentUser, school_id: UUID, result_id: UUID, data: ResultUpdate) -> Result:
        row = await self.get(user, school_id, result_id)
        exam = (await self.db.execute(select(Exam).where(Exam.id == row.exam_id))).scalar_one()
        if data.score is not None:
            self._apply_score(row, exam, data.score)
        if data.remarks is not None:
            row.remarks = data.remarks
        await self.db.flush()
        return row
