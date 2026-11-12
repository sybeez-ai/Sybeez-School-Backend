"""Exam prep: timed mock tests a student builds from their class's released MCQ questions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ExamMockAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One mock test. ``questions`` never holds answer keys; ``review`` (with keys) is written only at submit."""

    __tablename__ = "exam_mock_attempts"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_PROGRESS")  # IN_PROGRESS|SUBMITTED
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_reason: Mapped[str | None] = mapped_column(String(16), nullable=True)  # MANUAL|TIMED_OUT
    questions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    answers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    topic_results: Mapped[list | None] = mapped_column(JSONB, nullable=True)
