"""Parent language / WhatsApp consent, cached notice translations, and student progress updates."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ParentMessagePreference(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per parent per school: preferred language and explicit WhatsApp opt-in (with proof)."""

    __tablename__ = "parent_message_preferences"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_parent_msg_pref_school_user"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    preferred_language: Mapped[str] = mapped_column(String(8), nullable=False, default="en")
    whatsapp_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    whatsapp_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    whatsapp_consent_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    whatsapp_consent_withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StudentProgressUpdate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A teacher/admin note to a student's parents about progress or results."""

    __tablename__ = "student_progress_updates"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="PROGRESS")  # PROGRESS | RESULT
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageTranslation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cached translation of one notice into one language. The original is never overwritten."""

    __tablename__ = "message_translations"
    __table_args__ = (
        UniqueConstraint("school_id", "source_type", "source_id", "language", name="uq_message_translation"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)  # announcement | progress_update
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Copied from the source so row-level security can scope without joining.
    class_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="AUTO")  # AUTO | EDITED
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
