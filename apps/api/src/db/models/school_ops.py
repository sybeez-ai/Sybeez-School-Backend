"""Principal operations: substitute cover assignments."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, Text, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SubstituteAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One lesson (timetable entry on a date) covered by a substitute teacher."""

    __tablename__ = "substitute_assignments"
    __table_args__ = (
        Index(
            "uq_substitute_active_lesson",
            "timetable_entry_id",
            "cover_date",
            unique=True,
            postgresql_where=text("status = 'CONFIRMED'"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timetable_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("timetable_entries.id", ondelete="CASCADE"), nullable=False
    )
    cover_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    absent_teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    substitute_teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="CONFIRMED")  # CONFIRMED | CANCELLED
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
