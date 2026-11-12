"""Voice reading buddy (Classes 1-5): passages, sessions and per-student progress."""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ReadingPassage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """school_id NULL is the seeded library every school can read; school rows are the school's own."""

    __tablename__ = "reading_passages"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    class_level: Mapped[int] = mapped_column(Integer, nullable=False, index=True)  # 1..5
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="LIBRARY")  # LIBRARY | SCHOOL
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ReadingSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reading_sessions"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    passage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reading_passages.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="STARTED")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Transcript of the child's reading only; blanked when retention_until passes.
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    words_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words_correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words_misread: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    words_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    wcpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    retention_until: Mapped[date] = mapped_column(Date, nullable=False)


class ReadingProgress(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "reading_progress"
    __table_args__ = (UniqueConstraint("school_id", "student_id", name="uq_reading_progress_student"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stars_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_read_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    sessions_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    best_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
