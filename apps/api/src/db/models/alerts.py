import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, Time
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AlertMessageTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-school WhatsApp template and DLT text. school_id NULL is the platform default."""

    __tablename__ = "alert_message_templates"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    locale: Mapped[str] = mapped_column(String(8), nullable=False)
    whatsapp_template_name: Mapped[str] = mapped_column(String(128), nullable=False)
    language_code: Mapped[str] = mapped_column(String(16), nullable=False)
    variables: Mapped[list] = mapped_column(JSONB, nullable=False)
    sms_dlt_template_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    sms_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="UTILITY")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SchoolAlertSettings(Base, TimestampMixin):
    __tablename__ = "school_alert_settings"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True
    )
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quiet_hours_start: Mapped[time] = mapped_column(Time, nullable=False)
    quiet_hours_end: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Kolkata")
    default_locale: Mapped[str] = mapped_column(String(8), nullable=False, default="en")


class AlertInboundMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alert_inbound_messages"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    from_phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    opt_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertChannelOptOut(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alert_channel_opt_outs"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="STOP")
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertAuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alert_audit_logs"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
