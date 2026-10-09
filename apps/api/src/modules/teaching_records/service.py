from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError
from src.db.models import ClassRoom, Enrollment, Student, Subject, Teacher, TeachingRecord, User
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
