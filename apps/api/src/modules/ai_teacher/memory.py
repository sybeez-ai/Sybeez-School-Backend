"""Board pictures and memory the AI service writes. Teachers can read their section."""

from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core import storage
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import ClassRoom, Enrollment, Student
from src.db.models.daily_lesson import AiBoardPicture, AiClassFact, AiSessionMemory, AiStudentFact
from src.modules.daily_lessons.service import DailyLessonService, sniff
from src.shared.enums import EnrollmentStatus, UserRole

MAX_IMAGE = 2_000_000
FACT_KINDS = {"doubt", "misconception", "mastery", "style"}
CLASS_KINDS = {"taught_concept", "common_doubt"}


def _picture_out(row: AiBoardPicture) -> dict:
    signed = None
    try:
        signed = storage.signed_get_url(row.file_key)
    except Exception:  # noqa: BLE001 - a missing signer still leaves the private download route
        signed = None
    return {
        "id": str(row.id),
        "student_id": str(row.student_id),
        "class_id": str(row.class_id),
        "lesson_id": str(row.lesson_id) if row.lesson_id else None,
        "taught_on": row.taught_on.isoformat(),
        "topic": row.topic,
        "mime_type": row.mime_type,
        "scene_spec": row.scene_spec,
        "signed_url": signed,
    }


class AiTeacherMemory:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.lessons = DailyLessonService(db)

    async def _class_ok(self, school_id: UUID, class_id: UUID) -> ClassRoom:
        classroom = await self.db.get(ClassRoom, class_id)
        if classroom is None or classroom.school_id != school_id:
            raise NotFoundError("Class not found")
        return classroom

    async def _student_in_class(self, school_id: UUID, student_id: UUID, class_id: UUID) -> Student:
        student = await self.db.get(Student, student_id)
        if student is None or student.school_id != school_id:
            raise NotFoundError("Student not found")
        enrolled = (
            await self.db.execute(
                select(Enrollment.id).where(
                    Enrollment.school_id == school_id,
                    Enrollment.student_id == student_id,
                    Enrollment.class_id == class_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                ).limit(1)
            )
        ).first()
        if not enrolled:
            raise ValidationError("That student is not in this section")
        return student

    async def teacher_can_read_class(self, user: CurrentUser, class_id: UUID) -> None:
        if user.role == UserRole.SCHOOL_ADMIN:
            await self._class_ok(user.school_id, class_id)
            return
        if user.role == UserRole.TEACHER and await self.lessons._teacher_of_class(user.school_id, user.id, class_id):
            return
        raise ForbiddenError("You do not teach this section")

    async def store_picture(
        self,
        *,
        school_id: UUID,
        student_id: UUID,
        class_id: UUID,
        taught_on: date,
        topic: str,
        data: bytes,
        lesson_id: UUID | None = None,
        scene_spec: dict | None = None,
    ) -> dict:
        await self._student_in_class(school_id, student_id, class_id)
        if not data or len(data) > MAX_IMAGE:
            raise ValidationError("Picture must be between 1 byte and 2 MB")
        mime = sniff(data)
        if mime not in {"image/png", "image/jpeg"}:
            raise ValidationError("Board pictures must be PNG or JPEG")
        picture_id = uuid4()
        ext = "png" if mime == "image/png" else "jpg"
        key = storage.put_object(
            key=f"schools/{school_id}/ai-board/{picture_id}.{ext}",
            data=data,
            content_type=mime,
        )
        row = AiBoardPicture(
            id=picture_id,
            school_id=school_id,
            student_id=student_id,
            class_id=class_id,
            lesson_id=lesson_id,
            taught_on=taught_on,
            topic=topic.strip()[:200],
            file_key=key,
            mime_type=mime,
            scene_spec=scene_spec,
        )
        self.db.add(row)
        await self.db.flush()
        return _picture_out(row)

    async def list_pictures(self, user: CurrentUser, *, class_id: UUID, student_id: UUID | None) -> list[dict]:
        await self.teacher_can_read_class(user, class_id)
        stmt = select(AiBoardPicture).where(
            AiBoardPicture.school_id == user.school_id, AiBoardPicture.class_id == class_id
        )
        if student_id:
            stmt = stmt.where(AiBoardPicture.student_id == student_id)
        rows = (await self.db.execute(stmt.order_by(AiBoardPicture.created_at.desc()).limit(100))).scalars().all()
        return [_picture_out(r) for r in rows]

    async def read_picture(self, user: CurrentUser, picture_id: UUID) -> tuple[bytes, str]:
        row = await self.db.get(AiBoardPicture, picture_id)
        if row is None or row.school_id != user.school_id:
            raise NotFoundError("Picture not found")
        await self.teacher_can_read_class(user, row.class_id)
        try:
            data = storage.read_object(row.file_key)
        except FileNotFoundError as exc:
            raise NotFoundError("Picture is missing") from exc
        return data, row.mime_type

    async def add_student_fact(self, school_id: UUID, student_id: UUID, kind: str, content: str, concept: str | None, confidence: Decimal | None) -> dict:
        if kind not in FACT_KINDS:
            raise ValidationError("kind must be doubt, misconception, mastery, or style")
        student = await self.db.get(Student, student_id)
        if student is None or student.school_id != school_id:
            raise NotFoundError("Student not found")
        row = AiStudentFact(
            id=uuid4(), school_id=school_id, student_id=student_id, kind=kind,
            concept=concept, content=content.strip(), confidence=confidence,
        )
        self.db.add(row)
        await self.db.flush()
        return {"id": str(row.id), "kind": row.kind, "concept": row.concept, "content": row.content}

    async def student_facts(self, user: CurrentUser, student_id: UUID) -> list[dict]:
        student = await self.db.get(Student, student_id)
        if student is None or student.school_id != user.school_id:
            raise NotFoundError("Student not found")
        enrolled = (
            await self.db.execute(
                select(Enrollment.class_id).where(
                    Enrollment.school_id == user.school_id,
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE.value,
                )
            )
        ).scalars().all()
        teaches = False
        if user.role != UserRole.SCHOOL_ADMIN:
            for class_id in enrolled:
                if await self.lessons._teacher_of_class(user.school_id, user.id, class_id):
                    teaches = True
                    break
        if user.role != UserRole.SCHOOL_ADMIN and not teaches:
            raise ForbiddenError("You do not teach this student")
        rows = (
            await self.db.execute(
                select(AiStudentFact)
                .where(AiStudentFact.school_id == user.school_id, AiStudentFact.student_id == student_id)
                .order_by(AiStudentFact.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        return [{"id": str(r.id), "kind": r.kind, "concept": r.concept, "content": r.content, "confidence": str(r.confidence) if r.confidence is not None else None} for r in rows]

    async def add_class_fact(self, school_id: UUID, class_id: UUID, kind: str, content: str, concept: str | None, taught_on: date | None) -> dict:
        if kind not in CLASS_KINDS:
            raise ValidationError("kind must be taught_concept or common_doubt")
        await self._class_ok(school_id, class_id)
        row = AiClassFact(
            id=uuid4(), school_id=school_id, class_id=class_id, kind=kind,
            concept=concept, content=content.strip(), taught_on=taught_on,
        )
        self.db.add(row)
        await self.db.flush()
        return {"id": str(row.id), "kind": row.kind, "concept": row.concept, "content": row.content}

    async def class_facts(self, user: CurrentUser, class_id: UUID) -> list[dict]:
        await self.teacher_can_read_class(user, class_id)
        rows = (
            await self.db.execute(
                select(AiClassFact)
                .where(AiClassFact.school_id == user.school_id, AiClassFact.class_id == class_id)
                .order_by(AiClassFact.created_at.desc())
                .limit(100)
            )
        ).scalars().all()
        return [{"id": str(r.id), "kind": r.kind, "concept": r.concept, "content": r.content, "taught_on": r.taught_on.isoformat() if r.taught_on else None} for r in rows]

    async def save_session(self, school_id: UUID, session_id: UUID, student_id: UUID, class_id: UUID, summary: str, facts: dict) -> dict:
        await self._student_in_class(school_id, student_id, class_id)
        row = (
            await self.db.execute(
                select(AiSessionMemory).where(
                    AiSessionMemory.school_id == school_id, AiSessionMemory.session_id == session_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = AiSessionMemory(
                id=uuid4(), school_id=school_id, session_id=session_id, student_id=student_id,
                class_id=class_id, summary=summary, facts=facts or {},
            )
            self.db.add(row)
        else:
            row.summary = summary
            row.facts = facts or {}
            row.student_id = student_id
            row.class_id = class_id
        await self.db.flush()
        return {"id": str(row.id), "session_id": str(row.session_id), "summary": row.summary}

    async def sessions(self, user: CurrentUser, class_id: UUID) -> list[dict]:
        await self.teacher_can_read_class(user, class_id)
        rows = (
            await self.db.execute(
                select(AiSessionMemory)
                .where(AiSessionMemory.school_id == user.school_id, AiSessionMemory.class_id == class_id)
                .order_by(AiSessionMemory.updated_at.desc())
                .limit(50)
            )
        ).scalars().all()
        return [{"id": str(r.id), "session_id": str(r.session_id), "student_id": str(r.student_id), "summary": r.summary, "facts": r.facts} for r in rows]


def decode_image(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError("Picture is not valid base64") from exc
