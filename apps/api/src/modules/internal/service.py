from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import Enrollment, Homework, Student, Subject, TeachingRecord
from src.modules.internal.schemas import (
    AIContextResponse,
    CurrentLearning,
    HomeworkRef,
    SchoolContext,
    StudentContext,
    SubjectRef,
)


class InternalAIContextService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(self, *, user_id: UUID, school_id: UUID, subject_id: UUID | None = None) -> AIContextResponse:
        result = await self.db.execute(
            select(Student).where(Student.user_id == user_id, Student.school_id == school_id)
        )
        student = result.scalar_one_or_none()
        if student is None:
            # Platform/dev: allow user_id as opaque student ref when profile missing
            return AIContextResponse(
                student=StudentContext(id=user_id, class_id=None),
                school=SchoolContext(id=school_id),
                subjects=[],
                current_learning=CurrentLearning(topic="General study", taught_content=[]),
                homework=[],
            )

        class_id = None
        enr = await self.db.execute(
            select(Enrollment).where(Enrollment.student_id == student.id, Enrollment.school_id == school_id).limit(1)
        )
        enrollment = enr.scalar_one_or_none()
        if enrollment:
            class_id = enrollment.class_id

        subjects_q = select(Subject).where(Subject.school_id == school_id)
        subjects = list((await self.db.execute(subjects_q)).scalars().all())

        tr_q = select(TeachingRecord).where(TeachingRecord.school_id == school_id)
        if class_id:
            tr_q = tr_q.where(TeachingRecord.class_id == class_id)
        if subject_id:
            tr_q = tr_q.where(TeachingRecord.subject_id == subject_id)
        tr_q = tr_q.order_by(TeachingRecord.taught_on.desc()).limit(1)
        teaching = (await self.db.execute(tr_q)).scalar_one_or_none()

        current = None
        if teaching:
            content = [x.strip() for x in (teaching.summary or "").split(".") if x.strip()][:8]
            if teaching.topic:
                content = [teaching.topic] + content
            current = CurrentLearning(
                subject_id=teaching.subject_id,
                topic=teaching.topic,
                teaching_record_id=teaching.id,
                taught_content=content or [teaching.topic],
            )

        hw_q = select(Homework).where(Homework.school_id == school_id)
        if class_id:
            hw_q = hw_q.where(Homework.class_id == class_id)
        hw_q = hw_q.order_by(Homework.created_at.desc()).limit(5)
        homeworks = list((await self.db.execute(hw_q)).scalars().all())

        return AIContextResponse(
            student=StudentContext(id=student.id, class_id=class_id),
            school=SchoolContext(id=school_id),
            subjects=[SubjectRef(id=s.id, name=s.name) for s in subjects],
            current_learning=current,
            homework=[
                HomeworkRef(id=h.id, title=h.title, due_at=h.due_at.isoformat() if h.due_at else None)
                for h in homeworks
            ],
        )
