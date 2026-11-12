from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import (
    ClassRoom,
    Enrollment,
    Student,
    Subject,
    Syllabus,
    SyllabusTopic,
    Teacher,
    TeacherAssignment,
    TeachingRecord,
    User,
)
from src.modules.teaching_records.repository import TeachingRecordRepository
from src.modules.teaching_records.schemas import TeachingRecordCreate, TeachingRecordOut
from src.shared.enums import EnrollmentStatus


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

    async def assert_teaches(
        self, *, school_id: UUID, teacher: Teacher, class_id: UUID, subject_id: UUID | None = None
    ) -> None:
        """Teacher must hold a teaching assignment for the section (and subject, when given)
        or be the section's class teacher."""
        classroom = await self.db.get(ClassRoom, class_id)
        if classroom is None or classroom.school_id != school_id:
            raise NotFoundError("Section not found")
        if classroom.class_teacher_id == teacher.id:
            return
        stmt = select(TeacherAssignment.id).where(
            TeacherAssignment.school_id == school_id,
            TeacherAssignment.teacher_id == teacher.id,
            TeacherAssignment.class_id == class_id,
        )
        if subject_id is not None:
            stmt = stmt.where(TeacherAssignment.subject_id == subject_id)
        if (await self.db.execute(stmt.limit(1))).scalar_one_or_none() is None:
            raise ForbiddenError("You are not assigned to this section/subject")

    async def assert_user_teaches_class(self, *, school_id: UUID, user_id: UUID, class_id: UUID) -> None:
        teacher = await self._teacher_for_user(school_id, user_id)
        await self.assert_teaches(school_id=school_id, teacher=teacher, class_id=class_id)

    async def _validate_topic(self, *, school_id: UUID, subject_id: UUID, topic_id: UUID) -> None:
        row = (
            await self.db.execute(
                select(Syllabus.subject_id)
                .join(SyllabusTopic, SyllabusTopic.syllabus_id == Syllabus.id)
                .where(SyllabusTopic.id == topic_id, SyllabusTopic.school_id == school_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Syllabus topic not found")
        if row != subject_id:
            raise ForbiddenError("Syllabus topic belongs to a different subject")

    async def _student_class_id(self, school_id: UUID, user_id: UUID) -> UUID:
        student = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if student is None:
            raise ForbiddenError("Student profile required")
        enr = (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.student_id == student.id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if enr is None:
            raise ForbiddenError("No active enrollment")
        return enr.class_id

    async def _enrich(self, rows: list[TeachingRecord]) -> list[TeachingRecordOut]:
        if not rows:
            return []
        subject_ids = {r.subject_id for r in rows}
        class_ids = {r.class_id for r in rows}
        teacher_ids = {r.teacher_id for r in rows}

        subjects = {
            s.id: s.name
            for s in (
                await self.db.execute(select(Subject).where(Subject.id.in_(subject_ids)))
            ).scalars().all()
        }
        classes = {
            c.id: f"{c.name}{f'-{c.section}' if c.section else ''}"
            for c in (
                await self.db.execute(select(ClassRoom).where(ClassRoom.id.in_(class_ids)))
            ).scalars().all()
        }
        teachers = (
            await self.db.execute(select(Teacher).where(Teacher.id.in_(teacher_ids)))
        ).scalars().all()
        user_ids = {t.user_id for t in teachers}
        users = {
            u.id: u.full_name
            for u in (
                await self.db.execute(select(User).where(User.id.in_(user_ids)))
            ).scalars().all()
            if user_ids
        }
        teacher_names = {t.id: users.get(t.user_id) for t in teachers}

        out: list[TeachingRecordOut] = []
        for r in rows:
            base = TeachingRecordOut.model_validate(r)
            out.append(
                base.model_copy(
                    update={
                        "subject_name": subjects.get(r.subject_id),
                        "class_name": classes.get(r.class_id),
                        "teacher_name": teacher_names.get(r.teacher_id),
                    }
                )
            )
        return out

    async def create(self, *, school_id: UUID, user_id: UUID, data: TeachingRecordCreate) -> TeachingRecordOut:
        teacher = await self._teacher_for_user(school_id, user_id)
        await self.assert_teaches(
            school_id=school_id, teacher=teacher, class_id=data.class_id, subject_id=data.subject_id
        )
        if data.syllabus_topic_id is not None:
            await self._validate_topic(
                school_id=school_id, subject_id=data.subject_id, topic_id=data.syllabus_topic_id
            )
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
            syllabus_topic_id=data.syllabus_topic_id,
        )
        created = await self.repo.create(record)
        rows = await self._enrich([created])
        return rows[0]

    async def list_for_class(self, *, school_id: UUID, class_id: UUID) -> list[TeachingRecordOut]:
        rows = await self.repo.list_for_class(school_id=school_id, class_id=class_id, limit=100)
        return await self._enrich(rows)

    async def list_all(self, *, school_id: UUID) -> list[TeachingRecordOut]:
        rows = await self.repo.list_for_school(school_id=school_id, limit=200)
        return await self._enrich(rows)

    async def list_mine(self, *, school_id: UUID, user_id: UUID) -> list[TeachingRecordOut]:
        teacher = await self._teacher_for_user(school_id, user_id)
        rows = await self.repo.list_for_teacher(
            school_id=school_id, teacher_id=teacher.id, limit=100
        )
        return await self._enrich(rows)

    async def list_for_student(self, *, school_id: UUID, user_id: UUID) -> list[TeachingRecordOut]:
        class_id = await self._student_class_id(school_id, user_id)
        return await self.list_for_class(school_id=school_id, class_id=class_id)
