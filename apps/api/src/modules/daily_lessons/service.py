"""One day's teaching for one class section."""

from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core import storage
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import ClassRoom, Enrollment, Parent, Student, StudentParent, Subject, Teacher, TeacherAssignment
from src.db.models.daily_lesson import DailyLesson, DailyLessonFile
from src.shared.enums import EnrollmentStatus, UserRole

MAX_BYTES = 10_000_000
_SIGS = ((b"%PDF-", "application/pdf"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"))


def sniff(data: bytes) -> str | None:
    for sig, mime in _SIGS:
        if data.startswith(sig):
            return mime
    return None


class DailyLessonService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _teacher_of_class(self, school_id: UUID, user_id: UUID, class_id: UUID) -> bool:
        teacher = (
            await self.db.execute(
                select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user_id)
            )
        ).scalar_one_or_none()
        if teacher is None:
            return False
        classroom = await self.db.get(ClassRoom, class_id)
        if classroom and classroom.class_teacher_id == teacher.id and classroom.school_id == school_id:
            return True
        assigned = (
            await self.db.execute(
                select(TeacherAssignment.id).where(
                    TeacherAssignment.school_id == school_id,
                    TeacherAssignment.teacher_id == teacher.id,
                    TeacherAssignment.class_id == class_id,
                ).limit(1)
            )
        ).first()
        return assigned is not None

    async def _student(self, school_id: UUID, user_id: UUID) -> Student | None:
        return (
            await self.db.execute(
                select(Student).where(Student.school_id == school_id, Student.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def _in_section(self, school_id: UUID, student_id: UUID, class_id: UUID) -> bool:
        row = (
            await self.db.execute(
                select(Enrollment.id).where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == student_id,
                    Enrollment.class_id == class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                ).limit(1)
            )
        ).first()
        return row is not None

    async def _parent_of_section(self, school_id: UUID, user_id: UUID, class_id: UUID) -> bool:
        parent = (
            await self.db.execute(select(Parent).where(Parent.school_id == school_id, Parent.user_id == user_id))
        ).scalar_one_or_none()
        if parent is None:
            return False
        row = (
            await self.db.execute(
                select(StudentParent.id)
                .join(Enrollment, Enrollment.student_id == StudentParent.student_id)
                .where(
                    StudentParent.school_id == school_id,
                    StudentParent.parent_id == parent.id,
                    Enrollment.class_id == class_id,
                    Enrollment.school_id == school_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
                .limit(1)
            )
        ).first()
        return row is not None

    async def can_read(self, user: CurrentUser, lesson: DailyLesson) -> bool:
        if user.school_id != lesson.school_id:
            return False
        if user.role in {UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN}:
            return True
        if user.role == UserRole.TEACHER:
            return user.id == lesson.teacher_user_id or await self._teacher_of_class(
                lesson.school_id, user.id, lesson.class_id
            )
        if user.role == UserRole.STUDENT:
            student = await self._student(lesson.school_id, user.id)
            return bool(student and await self._in_section(lesson.school_id, student.id, lesson.class_id))
        if user.role == UserRole.PARENT:
            return await self._parent_of_section(lesson.school_id, user.id, lesson.class_id)
        return False

    async def can_write(self, user: CurrentUser, school_id: UUID, class_id: UUID) -> bool:
        if user.school_id != school_id:
            return False
        if user.role == UserRole.SCHOOL_ADMIN:
            return True
        if user.role == UserRole.TEACHER:
            return await self._teacher_of_class(school_id, user.id, class_id)
        return False

    def _out(self, lesson: DailyLesson, files: list[DailyLessonFile] | None = None) -> dict:
        body = {
            "id": str(lesson.id),
            "school_id": str(lesson.school_id),
            "class_id": str(lesson.class_id),
            "subject_id": str(lesson.subject_id),
            "teacher_user_id": str(lesson.teacher_user_id),
            "taught_on": lesson.taught_on.isoformat(),
            "chapter": lesson.chapter,
            "page_from": lesson.page_from,
            "page_to": lesson.page_to,
            "concept": lesson.concept,
            "syllabus_topic_id": str(lesson.syllabus_topic_id) if lesson.syllabus_topic_id else None,
            "taught_text": lesson.taught_text,
            "homework_text": lesson.homework_text,
            "files": [
                {
                    "id": str(f.id),
                    "file_name": f.file_name,
                    "mime_type": f.mime_type,
                    "file_size": f.file_size,
                }
                for f in (files or [])
            ],
        }
        return body

    async def create(self, user: CurrentUser, data) -> dict:
        classroom = await self.db.get(ClassRoom, data.class_id)
        if classroom is None or classroom.school_id != user.school_id:
            raise NotFoundError("Class not found")
        subject = await self.db.get(Subject, data.subject_id)
        if subject is None or subject.school_id != user.school_id:
            raise NotFoundError("Subject not found")
        if not await self.can_write(user, user.school_id, classroom.id):
            raise ForbiddenError("You do not teach this section")
        existing = (
            await self.db.execute(
                select(DailyLesson).where(
                    DailyLesson.school_id == user.school_id,
                    DailyLesson.class_id == classroom.id,
                    DailyLesson.subject_id == subject.id,
                    DailyLesson.taught_on == data.taught_on,
                )
            )
        ).scalar_one_or_none()
        if existing:
            if not await self.can_read(user, existing):
                raise ForbiddenError("You do not teach this section")
            existing.chapter = data.chapter
            existing.page_from = data.page_from
            existing.page_to = data.page_to
            existing.concept = data.concept
            existing.syllabus_topic_id = data.syllabus_topic_id
            existing.taught_text = data.taught_text.strip()
            existing.homework_text = (data.homework_text or "").strip()
            await self.db.flush()
            return self._out(existing, await self._files(existing.id))
        lesson = DailyLesson(
            id=uuid4(),
            school_id=user.school_id,
            class_id=classroom.id,
            subject_id=subject.id,
            teacher_user_id=user.id,
            taught_on=data.taught_on,
            chapter=data.chapter,
            page_from=data.page_from,
            page_to=data.page_to,
            concept=data.concept,
            syllabus_topic_id=data.syllabus_topic_id,
            taught_text=data.taught_text.strip(),
            homework_text=(data.homework_text or "").strip(),
        )
        self.db.add(lesson)
        await self.db.flush()
        return self._out(lesson, [])

    async def _files(self, lesson_id: UUID) -> list[DailyLessonFile]:
        return list(
            (
                await self.db.execute(
                    select(DailyLessonFile).where(DailyLessonFile.lesson_id == lesson_id)
                )
            ).scalars().all()
        )

    async def list_for(self, user: CurrentUser, *, class_id: UUID | None, taught_on: date | None) -> list[dict]:
        stmt = select(DailyLesson).where(DailyLesson.school_id == user.school_id)
        if class_id:
            stmt = stmt.where(DailyLesson.class_id == class_id)
        if taught_on:
            stmt = stmt.where(DailyLesson.taught_on == taught_on)
        rows = (await self.db.execute(stmt.order_by(DailyLesson.taught_on.desc()).limit(100))).scalars().all()
        visible = []
        for lesson in rows:
            if await self.can_read(user, lesson):
                visible.append(self._out(lesson, await self._files(lesson.id)))
        return visible

    async def get(self, user: CurrentUser, lesson_id: UUID) -> dict:
        lesson = await self.db.get(DailyLesson, lesson_id)
        if lesson is None or lesson.school_id != user.school_id or not await self.can_read(user, lesson):
            raise NotFoundError("Lesson not found")
        return self._out(lesson, await self._files(lesson.id))

    async def add_file(self, user: CurrentUser, lesson_id: UUID, filename: str, data: bytes) -> dict:
        lesson = await self.db.get(DailyLesson, lesson_id)
        if lesson is None or lesson.school_id != user.school_id or not await self.can_read(user, lesson):
            raise NotFoundError("Lesson not found")
        if not await self.can_write(user, lesson.school_id, lesson.class_id):
            raise ForbiddenError("You do not teach this section")
        if not data or len(data) > MAX_BYTES:
            raise ValidationError("File must be between 1 byte and 10 MB")
        mime = sniff(data)
        if mime is None:
            raise ValidationError("Only PDF, PNG or JPEG files are accepted")
        file_id = uuid4()
        ext = {"application/pdf": "pdf", "image/png": "png", "image/jpeg": "jpg"}[mime]
        key = storage.put_object(
            key=f"schools/{lesson.school_id}/daily-lessons/{lesson.id}/{file_id}.{ext}",
            data=data,
            content_type=mime,
        )
        row = DailyLessonFile(
            id=file_id,
            school_id=lesson.school_id,
            lesson_id=lesson.id,
            file_key=key,
            file_name=(filename or f"page.{ext}").replace("/", "_")[:255],
            mime_type=mime,
            file_size=len(data),
        )
        self.db.add(row)
        await self.db.flush()
        return {"id": str(row.id), "file_name": row.file_name, "mime_type": row.mime_type, "file_size": row.file_size}

    async def read_file(self, user: CurrentUser, lesson_id: UUID, file_id: UUID) -> tuple[bytes, str, str]:
        lesson = await self.db.get(DailyLesson, lesson_id)
        if lesson is None or lesson.school_id != user.school_id or not await self.can_read(user, lesson):
            raise NotFoundError("Lesson not found")
        row = await self.db.get(DailyLessonFile, file_id)
        if row is None or row.lesson_id != lesson.id:
            raise NotFoundError("File not found")
        try:
            data = storage.read_object(row.file_key)
        except FileNotFoundError as exc:
            raise NotFoundError("File is missing") from exc
        return data, row.mime_type, row.file_name
