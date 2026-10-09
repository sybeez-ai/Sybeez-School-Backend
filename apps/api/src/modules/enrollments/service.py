from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import AcademicYear, ClassRoom, Enrollment, Grade, Student, User
from src.modules.enrollments.schemas import EnrollmentCreate, EnrollmentOut
from src.shared.enums import EnrollmentStatus
from src.shared.utils import utcnow


class EnrollmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: EnrollmentCreate) -> EnrollmentOut:
        student = (
            await self.db.execute(
                select(Student).where(Student.id == data.student_id, Student.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not student:
            raise NotFoundError("Student not found")

        classroom = await self.db.get(ClassRoom, data.class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")

        year_id = data.academic_year_id or classroom.academic_year_id
        existing = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.student_id == data.student_id,
                    Enrollment.academic_year_id == year_id,
                )
            )
        ).scalar_one_or_none()
        if existing and existing.status == EnrollmentStatus.ACTIVE.value:
            raise ConflictError("Student already enrolled for this academic year")

        # Complete prior active year enrollment when starting a new year
        active = (
            await self.db.execute(
                select(Enrollment).where(
                    Enrollment.student_id == data.student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalar_one_or_none()
        if active and active.academic_year_id != year_id:
            active.status = EnrollmentStatus.COMPLETED.value
            active.left_at = utcnow()

        grade_id = data.grade_id or classroom.grade_id
        if existing:
            existing.class_id = classroom.id
            existing.grade_id = grade_id
            existing.roll_number = data.roll_number
            existing.status = EnrollmentStatus.ACTIVE.value
            existing.left_at = None
            existing.enrolled_at = utcnow()
            row = existing
        else:
            row = Enrollment(
                school_id=school_id,
                student_id=data.student_id,
                class_id=classroom.id,
                academic_year_id=year_id,
                grade_id=grade_id,
                roll_number=data.roll_number,
                status=EnrollmentStatus.ACTIVE.value,
            )
            self.db.add(row)
        await self.db.flush()
        return await self._to_out(row)

    async def list(
        self,
        school_id: UUID,
        class_id: UUID | None = None,
        student_id: UUID | None = None,
    ) -> list[EnrollmentOut]:
        q = select(Enrollment).where(Enrollment.school_id == school_id)
        if class_id:
            q = q.where(Enrollment.class_id == class_id)
        if student_id:
            q = q.where(Enrollment.student_id == student_id)
        result = await self.db.execute(q.order_by(Enrollment.created_at.desc()))
        out: list[EnrollmentOut] = []
        for row in result.scalars().all():
            out.append(await self._to_out(row))
        return out

    async def _to_out(self, row: Enrollment) -> EnrollmentOut:
        classroom = await self.db.get(ClassRoom, row.class_id)
        grade = await self.db.get(Grade, row.grade_id) if row.grade_id else None
        year = await self.db.get(AcademicYear, row.academic_year_id)
        student = await self.db.get(Student, row.student_id)
        student_name = None
        if student:
            user = await self.db.get(User, student.user_id)
            student_name = user.full_name if user else None
        return EnrollmentOut(
            id=row.id,
            school_id=row.school_id,
            student_id=row.student_id,
            class_id=row.class_id,
            academic_year_id=row.academic_year_id,
            grade_id=row.grade_id,
            roll_number=row.roll_number,
            status=row.status,
            enrolled_at=row.enrolled_at,
            left_at=row.left_at,
            notes=row.notes,
            created_at=row.created_at,
            class_name=classroom.name if classroom else None,
            section=classroom.section if classroom else None,
            grade_name=grade.name if grade else None,
            academic_year_name=year.name if year else None,
            student_name=student_name,
        )
