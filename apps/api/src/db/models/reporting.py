"""Phase 11 reporting metadata — does not duplicate academic/financial records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AnalyticsSetting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "analytics_settings"
    __table_args__ = (UniqueConstraint("school_id", name="uq_analytics_settings_school"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    attendance_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=75.0)
    missing_homework_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    low_exam_percentage_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=35.0)


class SavedReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "saved_reports"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    configuration_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ScheduledReport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Architecture for future scheduled delivery — not executed in Phase 11 MVP."""

    __tablename__ = "scheduled_reports"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    configuration_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    schedule: Mapped[str] = mapped_column(String(128), nullable=False, default="MONTHLY")
    recipients_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ReportAuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "report_audit_logs"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    filters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
