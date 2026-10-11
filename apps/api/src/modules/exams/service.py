from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Exam, Result
from src.modules.access.scope import restrict_classes
from src.modules.exams.schemas import ExamCreate, ExamUpdate
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


class ExamService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: ExamCreate) -> Exam:
        row = Exam(school_id=school_id, **data.model_dump())
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, user: CurrentUser, school_id: UUID, page: PageParams, class_id: UUID | None) -> tuple[list[Exam], int]:
        stmt = select(Exam).where(Exam.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Exam.class_id)
        if class_id:
            stmt = stmt.where(Exam.class_id == class_id)
        stmt = stmt.order_by(Exam.scheduled_at.desc().nullslast(), Exam.created_at.desc())
        result, total = await fetch_page(self.db, stmt, page)
        return list(result.scalars().all()), total

    async def get(self, user: CurrentUser, school_id: UUID, exam_id: UUID) -> Exam:
        stmt = select(Exam).where(Exam.id == exam_id, Exam.school_id == school_id)
        stmt = restrict_classes(stmt, user, school_id, Exam.class_id)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Exam not found")
        return row

    async def update(self, user: CurrentUser, school_id: UUID, exam_id: UUID, data: ExamUpdate) -> Exam:
        row = await self.get(user, school_id, exam_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(row, key, value)
        await self.db.flush()
        return row

    async def delete(self, user: CurrentUser, school_id: UUID, exam_id: UUID) -> None:
        row = await self.get(user, school_id, exam_id)
        count = (
            await self.db.execute(select(func.count()).select_from(Result).where(Result.exam_id == row.id))
        ).scalar_one()
        if count:
            raise ConflictError("Exam already has results")
        await self.db.delete(row)
        await self.db.flush()
