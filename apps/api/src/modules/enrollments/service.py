from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ClassRoom, Enrollment
from src.modules.enrollments.schemas import EnrollmentCreate


class EnrollmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: EnrollmentCreate) -> Enrollment:
        grade_id = data.grade_id
        if grade_id is None:
            classroom = await self.db.get(ClassRoom, data.class_id)
            if classroom and classroom.school_id == school_id:
                grade_id = classroom.grade_id

        row = Enrollment(
            school_id=school_id,
            student_id=data.student_id,
            class_id=data.class_id,
            academic_year_id=data.academic_year_id,
            grade_id=grade_id,
            status="ACTIVE",
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def list(self, school_id: UUID, class_id: UUID | None = None) -> list[Enrollment]:
        q = select(Enrollment).where(Enrollment.school_id == school_id)
        if class_id:
            q = q.where(Enrollment.class_id == class_id)
        result = await self.db.execute(q.order_by(Enrollment.created_at.desc()))
        return list(result.scalars().all())
