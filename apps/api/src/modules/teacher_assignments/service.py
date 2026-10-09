from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import (
    AcademicYear,
    ClassRoom,
    Enrollment,
    Grade,
    Subject,
    Teacher,
    TeacherAssignment,
    User,
)
from src.modules.teacher_assignments.schemas import TeacherAssignmentCreate, TeacherAssignmentOut
from src.shared.enums import EnrollmentStatus


class TeacherAssignmentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: TeacherAssignmentCreate) -> TeacherAssignmentOut:
        teacher = await self._get_teacher(school_id, data.teacher_id)
        subject = await self._get_subject(school_id, data.subject_id)
        classroom = await self._get_classroom(school_id, data.class_id)
        year_id = data.academic_year_id or classroom.academic_year_id
        if year_id:
            year = await self.db.get(AcademicYear, year_id)
            if not year or year.school_id != school_id:
                raise NotFoundError("Academic year not found")

        existing = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.subject_id == subject.id,
                    TeacherAssignment.class_id == classroom.id,
                    TeacherAssignment.academic_year_id == year_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("This teaching assignment already exists")

        row = TeacherAssignment(
            school_id=school_id,
            teacher_id=teacher.id,
            subject_id=subject.id,
            class_id=classroom.id,
            academic_year_id=year_id,
        )
        self.db.add(row)
        await self.db.flush()
        return await self._to_out(row)

    async def list(
        self,
        school_id: UUID,
        *,
        teacher_id: UUID | None = None,
        academic_year_id: UUID | None = None,
        class_id: UUID | None = None,
    ) -> list[TeacherAssignmentOut]:
        q = select(TeacherAssignment).where(TeacherAssignment.school_id == school_id)
        if teacher_id:
            q = q.where(TeacherAssignment.teacher_id == teacher_id)
        if academic_year_id:
            q = q.where(TeacherAssignment.academic_year_id == academic_year_id)
        if class_id:
            q = q.where(TeacherAssignment.class_id == class_id)
        result = await self.db.execute(q.order_by(TeacherAssignment.created_at.desc()))
        return [await self._to_out(row) for row in result.scalars().all()]

    async def list_mine(self, school_id: UUID, user_id: UUID) -> list[TeacherAssignmentOut]:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise NotFoundError("Teacher profile not found")
        return await self.list(school_id, teacher_id=teacher.id)

    async def delete(self, school_id: UUID, assignment_id: UUID) -> None:
        row = (
            await self.db.execute(
                select(TeacherAssignment).where(
                    TeacherAssignment.id == assignment_id,
                    TeacherAssignment.school_id == school_id,
                )
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError("Assignment not found")
        await self.db.delete(row)
        await self.db.flush()

    async def _get_teacher(self, school_id: UUID, teacher_id: UUID) -> Teacher:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.id == teacher_id, Teacher.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not teacher:
            raise NotFoundError("Teacher not found")
        return teacher

    async def _get_subject(self, school_id: UUID, subject_id: UUID) -> Subject:
        subject = (
            await self.db.execute(
                select(Subject).where(Subject.id == subject_id, Subject.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not subject:
            raise NotFoundError("Subject not found")
        return subject

    async def _get_classroom(self, school_id: UUID, class_id: UUID) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        return classroom

    async def _to_out(self, row: TeacherAssignment) -> TeacherAssignmentOut:
        teacher = await self.db.get(Teacher, row.teacher_id)
        subject = await self.db.get(Subject, row.subject_id)
        classroom = await self.db.get(ClassRoom, row.class_id)
        year = await self.db.get(AcademicYear, row.academic_year_id) if row.academic_year_id else None
        grade = None
        if classroom and classroom.grade_id:
            grade = await self.db.get(Grade, classroom.grade_id)
        teacher_name = None
        if teacher:
            user = await self.db.get(User, teacher.user_id)
            teacher_name = user.full_name if user else None
        count = int(
            (
                await self.db.execute(
                    select(func.count())
                    .select_from(Enrollment)
                    .where(
                        Enrollment.class_id == row.class_id,
                        Enrollment.status == EnrollmentStatus.ACTIVE.value,
                    )
                )
            ).scalar_one()
        )
        is_ct = bool(teacher and classroom and classroom.class_teacher_id == teacher.id)
        return TeacherAssignmentOut(
            id=row.id,
            school_id=row.school_id,
            teacher_id=row.teacher_id,
            subject_id=row.subject_id,
            class_id=row.class_id,
            academic_year_id=row.academic_year_id,
            academic_year_name=year.name if year else None,
            created_at=row.created_at,
            teacher_name=teacher_name,
            subject_name=subject.name if subject else None,
            class_name=classroom.name if classroom else None,
            section=classroom.section if classroom else None,
            grade_name=grade.name if grade else None,
            student_count=count,
            is_class_teacher=is_ct,
        )
