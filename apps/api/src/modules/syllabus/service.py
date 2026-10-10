from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import AcademicYear, Enrollment, Grade, Student, Subject, Syllabus, SyllabusTopic
from src.modules.syllabus.schemas import (
    SyllabusCreate,
    SyllabusOut,
    SyllabusTopicCreate,
    SyllabusTopicOut,
)
from src.shared.enums import EnrollmentStatus


class SyllabusService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, school_id: UUID, data: SyllabusCreate) -> SyllabusOut:
        existing = (
            await self.db.execute(
                select(Syllabus).where(
                    Syllabus.school_id == school_id,
                    Syllabus.academic_year_id == data.academic_year_id,
                    Syllabus.grade_id == data.grade_id,
                    Syllabus.subject_id == data.subject_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("Syllabus already exists for this year, class, and subject.")

        row = Syllabus(
            school_id=school_id,
            academic_year_id=data.academic_year_id,
            grade_id=data.grade_id,
            subject_id=data.subject_id,
            title=data.title.strip(),
            description=data.description,
        )
        self.db.add(row)
        await self.db.flush()
        return await self._to_out(row, include_topics=True)

    async def list(self, school_id: UUID) -> list[SyllabusOut]:
        result = await self.db.execute(
            select(Syllabus)
            .where(Syllabus.school_id == school_id)
            .order_by(Syllabus.created_at.desc())
        )
        out: list[SyllabusOut] = []
        for row in result.scalars().all():
            out.append(await self._to_out(row, include_topics=False))
        return out

    async def list_for_student(self, school_id: UUID, user_id: UUID) -> list[SyllabusOut]:
        st = (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not st:
            raise ForbiddenError("Student profile required")
        enr = (
            await self.db.execute(
                select(Enrollment)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == st.id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .order_by(Enrollment.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not enr:
            return []
        result = await self.db.execute(
            select(Syllabus)
            .where(
                Syllabus.school_id == school_id,
                Syllabus.academic_year_id == enr.academic_year_id,
                Syllabus.grade_id == enr.grade_id,
            )
            .order_by(Syllabus.created_at.desc())
        )
        out: list[SyllabusOut] = []
        for row in result.scalars().all():
            out.append(await self._to_out(row, include_topics=False))
        return out

    async def get(self, school_id: UUID, syllabus_id: UUID) -> SyllabusOut:
        row = await self._get(school_id, syllabus_id)
        return await self._to_out(row, include_topics=True)

    async def add_topic(
        self, school_id: UUID, syllabus_id: UUID, data: SyllabusTopicCreate
    ) -> SyllabusTopicOut:
        syllabus = await self._get(school_id, syllabus_id)
        order = data.order_index
        if order is None:
            count = len(
                (
                    await self.db.execute(
                        select(SyllabusTopic).where(SyllabusTopic.syllabus_id == syllabus.id)
                    )
                )
                .scalars()
                .all()
            )
            order = count + 1
        topic = SyllabusTopic(
            syllabus_id=syllabus.id,
            parent_topic_id=data.parent_topic_id,
            name=data.name.strip(),
            description=data.description,
            order_index=order,
        )
        self.db.add(topic)
        await self.db.flush()
        return SyllabusTopicOut(
            id=topic.id,
            syllabus_id=topic.syllabus_id,
            parent_topic_id=topic.parent_topic_id,
            name=topic.name,
            description=topic.description,
            order_index=topic.order_index,
            created_at=topic.created_at,
        )

    async def list_topics(self, school_id: UUID, syllabus_id: UUID) -> list[SyllabusTopicOut]:
        await self._get(school_id, syllabus_id)
        result = await self.db.execute(
            select(SyllabusTopic)
            .where(SyllabusTopic.syllabus_id == syllabus_id)
            .order_by(SyllabusTopic.order_index, SyllabusTopic.created_at)
        )
        return [
            SyllabusTopicOut(
                id=t.id,
                syllabus_id=t.syllabus_id,
                parent_topic_id=t.parent_topic_id,
                name=t.name,
                description=t.description,
                order_index=t.order_index,
                created_at=t.created_at,
            )
            for t in result.scalars().all()
        ]

    async def _get(self, school_id: UUID, syllabus_id: UUID) -> Syllabus:
        row = (
            await self.db.execute(
                select(Syllabus)
                .options(selectinload(Syllabus.topics))
                .where(Syllabus.id == syllabus_id, Syllabus.school_id == school_id)
            )
        ).scalar_one_or_none()
        if not row:
            raise NotFoundError("Syllabus not found")
        return row

    async def _to_out(self, row: Syllabus, *, include_topics: bool) -> SyllabusOut:
        year = await self.db.get(AcademicYear, row.academic_year_id)
        grade = await self.db.get(Grade, row.grade_id)
        subject = await self.db.get(Subject, row.subject_id)
        topics_q = await self.db.execute(
            select(SyllabusTopic)
            .where(SyllabusTopic.syllabus_id == row.id)
            .order_by(SyllabusTopic.order_index, SyllabusTopic.created_at)
        )
        topics = list(topics_q.scalars().all())
        topic_outs = [
            SyllabusTopicOut(
                id=t.id,
                syllabus_id=t.syllabus_id,
                parent_topic_id=t.parent_topic_id,
                name=t.name,
                description=t.description,
                order_index=t.order_index,
                created_at=t.created_at,
            )
            for t in topics
        ]
        return SyllabusOut(
            id=row.id,
            school_id=row.school_id,
            academic_year_id=row.academic_year_id,
            grade_id=row.grade_id,
            subject_id=row.subject_id,
            title=row.title,
            description=row.description,
            created_at=row.created_at,
            academic_year_name=year.name if year else None,
            grade_name=grade.name if grade else None,
            subject_name=subject.name if subject else None,
            subject_code=subject.code if subject else None,
            topic_count=len(topics),
            topics=topic_outs if include_topics else [],
        )
