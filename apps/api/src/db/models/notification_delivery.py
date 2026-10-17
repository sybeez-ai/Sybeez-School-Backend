import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """
    Outbound delivery record for SMS / WhatsApp / Email (future).

    In-app notifications remain in the `notifications` table. This table tracks
    provider delivery attempts + outcomes.
    """

    __tablename__ = "notification_deliveries"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # SMS | WHATSAPP | EMAIL (future)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    notification_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    to: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="mock")
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        nullable=True
    )  # populated by scheduler when implementing delayed retries
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    event_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    locale: Mapped[str | None] = mapped_column(String(8), nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    variables_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_deliveries.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The column is TIMESTAMPTZ (migration 0053); map it timezone-aware so asyncpg accepts
    # the aware UTC datetimes the alerts service binds.
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="text")

