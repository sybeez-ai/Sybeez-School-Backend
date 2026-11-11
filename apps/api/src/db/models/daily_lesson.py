"""What a teacher taught one section on one day, plus AI board pictures and memory.

A section is a class row (for example 6A). Only that section, their parents, the
teacher, and the school admin may read it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DailyLesson(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "daily_lessons"
    __table_args__ = (
        UniqueConstraint("school_id", "class_id", "subject_id", "taught_on", name="uq_daily_lesson_section_day"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    taught_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    chapter: Mapped[str | None] = mapped_column(String(200), nullable=True)
    page_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    concept: Mapped[str | None] = mapped_column(String(200), nullable=True)
    syllabus_topic_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    taught_text: Mapped[str] = mapped_column(Text, nullable=False)
    homework_text: Mapped[str] = mapped_column(Text, nullable=False, default="")


class DailyLessonFile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "daily_lesson_files"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_lessons.id", ondelete="CASCADE"), nullable=False, index=True)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)


class AiBoardPicture(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_board_pictures"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    lesson_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_lessons.id", ondelete="SET NULL"), nullable=True)
    taught_on: Mapped[date] = mapped_column(Date, nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    scene_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class AiStudentFact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per student: a doubt, a misconception, mastery, or a preferred style."""

    __tablename__ = "ai_student_facts"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # doubt misconception mastery style
    concept: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)


class AiClassFact(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_class_facts"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # taught_concept common_doubt
    concept: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    taught_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class AiSessionMemory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "ai_session_memory"
    __table_args__ = (UniqueConstraint("school_id", "session_id", name="uq_ai_session_memory"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
