"""WhatsApp AI assistant: optional extra numbers, sessions, transcripts, usage.

The phone saved on a user, parent, student, teacher, or staff profile is the
WhatsApp identity. Rows in whatsapp_links are only for a different number a
person linked with a one-time code.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WhatsAppAssistantSettings(Base, TimestampMixin):
    __tablename__ = "whatsapp_assistant_settings"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True
    )
    assistant_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    leave_requests_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class WhatsAppLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An extra WhatsApp number, linked with a one-time code. Profile phones are not stored here."""

    __tablename__ = "whatsapp_links"
    __table_args__ = (UniqueConstraint("user_id", "phone_e164", name="uq_whatsapp_links_user_phone"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class WhatsAppLinkCode(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "whatsapp_link_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WhatsAppSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "whatsapp_sessions"

    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pending: Mapped[str | None] = mapped_column(String(16), nullable=True)
    options_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    pending_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(String(32), nullable=True)


class WhatsAppMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        Index(
            "uq_whatsapp_messages_provider",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
    )

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("whatsapp_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="received")
    persona: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tool_trace: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    send_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)


class WhatsAppUsage(Base, TimestampMixin):
    __tablename__ = "whatsapp_usage"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), primary_key=True
    )
    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    inbound_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    generated_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    suppressed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
