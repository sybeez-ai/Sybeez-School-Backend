"""Online MCQ tests: tests, questions, answer keys, attempts, answers, violation log.

Answer keys live in their own table (online_test_question_keys) whose RLS policy only
admits school admins, the teachers of the test's class, and the service role. Students and
parents can never read it, even through a bug in an API response model.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _school_fk():
    return mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)


class OnlineTest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "online_tests"
    __table_args__ = (Index("ix_online_tests_school_class_status", "school_id", "class_id", "status"),)

    school_id: Mapped[uuid.UUID] = _school_fk()
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    syllabus_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", server_default="DRAFT")
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    opens_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    results_released_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reminded_24h_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reminded_1h_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OnlineTestQuestion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What a student may see. Options carry ids and text only, never correctness."""

    __tablename__ = "online_test_questions"

    school_id: Mapped[uuid.UUID] = _school_fk()
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list] = mapped_column(JSONB, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    difficulty: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL", server_default="MANUAL")
    ai_generation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    ai_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class OnlineTestQuestionKey(Base, TimestampMixin):
    __tablename__ = "online_test_question_keys"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_test_questions.id", ondelete="CASCADE"), primary_key=True
    )
    school_id: Mapped[uuid.UUID] = _school_fk()
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    correct_option_id: Mapped[str] = mapped_column(String(32), nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)


class OnlineTestAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "online_test_attempts"
    __table_args__ = (UniqueConstraint("test_id", "student_id", name="uq_online_test_attempt_student"),)

    school_id: Mapped[uuid.UUID] = _school_fk()
    test_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_tests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="IN_PROGRESS")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)
    question_order: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    option_order: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    violation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    resume_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE", server_default="NONE")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class OnlineTestAnswer(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "online_test_answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id", name="uq_online_test_answer"),)

    school_id: Mapped[uuid.UUID] = _school_fk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_test_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_test_questions.id", ondelete="CASCADE"), nullable=False
    )
    selected_option_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OnlineTestEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only violation / audit log for an attempt."""

    # Students insert events but cannot SELECT them, so never ask Postgres to RETURN server defaults.
    __mapper_args__ = {"eager_defaults": False}
    __tablename__ = "online_test_events"

    school_id: Mapped[uuid.UUID] = _school_fk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("online_test_attempts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    test_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    server_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp(), nullable=False)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class OnlineTestSchoolSettings(Base):
    __tablename__ = "online_test_school_settings"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    ai_monthly_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=200, server_default="200")
    default_max_violations: Mapped[int] = mapped_column(Integer, nullable=False, default=3, server_default="3")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OnlineTestAiUsage(Base, UUIDPrimaryKeyMixin):
    """One row per successful AI generation; the monthly cap sums `returned`."""

    __tablename__ = "online_test_ai_usage"

    school_id: Mapped[uuid.UUID] = _school_fk()
    teacher_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    test_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    requested: Mapped[int] = mapped_column(Integer, nullable=False)
    returned: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OnlineTestAuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "online_test_audit_logs"

    school_id: Mapped[uuid.UUID] = _school_fk()
    test_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
