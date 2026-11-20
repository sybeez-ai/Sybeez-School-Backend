from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import (
    AcademicYear,
    Enrollment,
    Grade,
    Parent,
    Student,
    StudentParent,
    Subject,
    Syllabus,
    SyllabusTopic,
)
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
        rows = (
            await self.db.execute(
                select(Syllabus)
                .where(Syllabus.school_id == school_id)
                .order_by(Syllabus.created_at.desc())
            )
        ).scalars().all()
        return await self._outs(list(rows), include_topics=False)

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
        return await self._outs(list(result.scalars().all()), include_topics=False)

    async def list_for_parent(self, school_id: UUID, user_id: UUID) -> list[SyllabusOut]:
        parent = (
            await self.db.execute(
                select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id)
            )
        ).scalar_one_or_none()
        if not parent:
            raise ForbiddenError("Parent profile required")
        child_ids = (
            await self.db.execute(
                select(StudentParent.student_id).where(
                    StudentParent.school_id == school_id, StudentParent.parent_id == parent.id
                )
            )
        ).scalars().all()
        if not child_ids:
            return []
        placements = (
            await self.db.execute(
                select(Enrollment.academic_year_id, Enrollment.grade_id)
                .where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id.in_(list(child_ids)),
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .distinct()
            )
        ).all()
        if not placements:
            return []
        clauses = [
            (Syllabus.academic_year_id == year_id) & (Syllabus.grade_id == grade_id)
            for year_id, grade_id in placements
            if grade_id is not None
        ]
        if not clauses:
            return []
        rows = (
            await self.db.execute(
                select(Syllabus)
                .where(Syllabus.school_id == school_id, or_(*clauses))
                .order_by(Syllabus.created_at.desc())
            )
        ).scalars().all()
        return await self._outs(list(rows), include_topics=False)

    async def get(self, school_id: UUID, syllabus_id: UUID) -> SyllabusOut:
        row = await self._get(school_id, syllabus_id)
        return await self._to_out(row, include_topics=True)

    async def add_topic(
        self, school_id: UUID, syllabus_id: UUID, data: SyllabusTopicCreate
    ) -> SyllabusTopicOut:
        syllabus = await self._get(school_id, syllabus_id)
        if data.parent_topic_id is not None:
            await self._assert_topic_parent(syllabus.id, data.parent_topic_id)
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
            school_id=school_id,
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

    async def _assert_topic_parent(self, syllabus_id: UUID, parent_topic_id: UUID) -> None:
        """The parent topic must be in this syllabus, and the chain must not loop."""
        seen: set[UUID] = set()
        current_id: UUID | None = parent_topic_id
        while current_id is not None:
            if current_id in seen:
                raise ConflictError("This topic would loop back on itself")
            seen.add(current_id)
            if len(seen) > 50:
                raise ConflictError("This topic is nested too deeply")
            row = (
                await self.db.execute(
                    select(SyllabusTopic).where(
                        SyllabusTopic.id == current_id, SyllabusTopic.syllabus_id == syllabus_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise NotFoundError("Parent topic not found")
            current_id = row.parent_topic_id

    async def _outs(self, rows: list[Syllabus], *, include_topics: bool) -> list[SyllabusOut]:
        if not rows:
            return []
        ids = [row.id for row in rows]
        years = {
            row.id: row
            for row in (
                await self.db.execute(
                    select(AcademicYear).where(AcademicYear.id.in_({r.academic_year_id for r in rows}))
                )
            ).scalars().all()
        }
        grades = {
            row.id: row
            for row in (
                await self.db.execute(select(Grade).where(Grade.id.in_({r.grade_id for r in rows})))
            ).scalars().all()
        }
        subjects = {
            row.id: row
            for row in (
                await self.db.execute(select(Subject).where(Subject.id.in_({r.subject_id for r in rows})))
            ).scalars().all()
        }
        counts = {
            sid: n
            for sid, n in (
                await self.db.execute(
                    select(SyllabusTopic.syllabus_id, func.count())
                    .where(SyllabusTopic.syllabus_id.in_(ids))
                    .group_by(SyllabusTopic.syllabus_id)
                )
            ).all()
        }
        topics_by: dict[UUID, list[SyllabusTopic]] = {}
        if include_topics:
            for topic in (
                await self.db.execute(
                    select(SyllabusTopic)
                    .where(SyllabusTopic.syllabus_id.in_(ids))
                    .order_by(SyllabusTopic.order_index, SyllabusTopic.created_at)
                )
            ).scalars().all():
                topics_by.setdefault(topic.syllabus_id, []).append(topic)
        out: list[SyllabusOut] = []
        for row in rows:
            year = years.get(row.academic_year_id)
            grade = grades.get(row.grade_id)
            subject = subjects.get(row.subject_id)
            topics = topics_by.get(row.id, [])
            out.append(
                SyllabusOut(
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
                    topic_count=int(counts.get(row.id, 0)),
                    topics=[
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
                    ],
                )
            )
        return out

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
