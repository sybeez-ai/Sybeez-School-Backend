from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError, ValidationError
from src.db.models import ClassRoom, Subject, Teacher, User
from src.db.models.class_subject import ClassSubject
from src.modules.class_subjects.schemas import ClassSubjectCreate, ClassSubjectOut, ClassSubjectUpdate
from src.shared.utils import format_class_label


class ClassSubjectService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _classroom(self, school_id: UUID, class_id: UUID) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if not classroom or classroom.school_id != school_id:
            raise NotFoundError("Class not found")
        return classroom

    async def _subject(self, school_id: UUID, subject_id: UUID) -> Subject:
        subject = await self.db.get(Subject, subject_id)
        if not subject or subject.school_id != school_id:
            raise NotFoundError("Subject not found")
        return subject

    async def _teacher_name(self, school_id: UUID, teacher_id: UUID | None) -> tuple[UUID | None, str | None]:
        if teacher_id is None:
            return None, None
        teacher = await self.db.get(Teacher, teacher_id)
        if not teacher or teacher.school_id != school_id:
            raise ValidationError("Teacher not found in this school")
        user = await self.db.get(User, teacher.user_id)
        return teacher.id, (user.full_name if user else None)

    async def _to_out(self, row: ClassSubject) -> ClassSubjectOut:
        classroom = await self.db.get(ClassRoom, row.class_id)
        subject = await self.db.get(Subject, row.subject_id)
        _tid, teacher_name = await self._teacher_name(row.school_id, row.teacher_id)
        return ClassSubjectOut(
            id=row.id,
            school_id=row.school_id,
            class_id=row.class_id,
            class_name=format_class_label(classroom.name if classroom else "", classroom.section if classroom else None),
            subject_id=row.subject_id,
            subject_name=subject.name if subject else "—",
            subject_code=subject.code if subject else "",
            teacher_id=row.teacher_id,
            teacher_name=teacher_name,
            created_at=row.created_at,
        )

    async def list_for_class(self, school_id: UUID, class_id: UUID) -> list[ClassSubjectOut]:
        await self._classroom(school_id, class_id)
        rows = (
            await self.db.execute(
                select(ClassSubject)
                .where(ClassSubject.school_id == school_id, ClassSubject.class_id == class_id)
                .order_by(ClassSubject.created_at)
            )
        ).scalars().all()
        return [await self._to_out(r) for r in rows]

    async def add(self, school_id: UUID, class_id: UUID, body: ClassSubjectCreate) -> ClassSubjectOut:
        await self._classroom(school_id, class_id)
        await self._subject(school_id, body.subject_id)
        teacher_id, _ = await self._teacher_name(school_id, body.teacher_id)
        existing = (
            await self.db.execute(
                select(ClassSubject).where(
                    ClassSubject.class_id == class_id,
                    ClassSubject.subject_id == body.subject_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("This subject is already on the class")
        row = ClassSubject(
            school_id=school_id,
            class_id=class_id,
            subject_id=body.subject_id,
            teacher_id=teacher_id,
        )
        self.db.add(row)
        await self.db.commit()
        await self.db.refresh(row)
        return await self._to_out(row)

    async def update(self, school_id: UUID, class_id: UUID, item_id: UUID, body: ClassSubjectUpdate) -> ClassSubjectOut:
        row = await self.db.get(ClassSubject, item_id)
        if not row or row.school_id != school_id or row.class_id != class_id:
            raise NotFoundError("Class subject not found")
        teacher_id, _ = await self._teacher_name(school_id, body.teacher_id)
        row.teacher_id = teacher_id
        await self.db.commit()
        await self.db.refresh(row)
        return await self._to_out(row)

    async def remove(self, school_id: UUID, class_id: UUID, item_id: UUID) -> None:
        row = await self.db.get(ClassSubject, item_id)
        if not row or row.school_id != school_id or row.class_id != class_id:
            raise NotFoundError("Class subject not found")
        await self.db.delete(row)
        await self.db.commit()
