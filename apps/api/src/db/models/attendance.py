import uuid
from datetime import date, datetime, time

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import AttendanceStatus


class Attendance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Day-level attendance for a student in a class (period optional for later)."""

    __tablename__ = "attendance"
    __table_args__ = (UniqueConstraint("student_id", "class_id", "attended_on", name="uq_attendance_day"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attended_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=AttendanceStatus.PRESENT.value, nullable=False)
    arrival_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_modified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AttendanceAuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable history of attendance status changes."""

    __tablename__ = "attendance_audit_logs"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attendance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attendance.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attended_on: Mapped[date] = mapped_column(Date, nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    leave_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leave_requests.id", ondelete="SET NULL"), nullable=True
    )


class AttendanceSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-school attendance rules and thresholds."""

    __tablename__ = "attendance_settings"
    __table_args__ = (UniqueConstraint("school_id", name="uq_attendance_settings_school"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    school_start_time: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 0))
    absence_alert_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    absence_alert_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    late_alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    low_attendance_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=85)
    teacher_can_approve_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_parents_on_absent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
