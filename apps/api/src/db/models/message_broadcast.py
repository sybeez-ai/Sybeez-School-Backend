import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MessageBroadcast(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One school-admin broadcast (WhatsApp / email / SMS) and its audience snapshot."""

    __tablename__ = "message_broadcasts"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    audience_type: Mapped[str] = mapped_column(String(32), nullable=False)
    audience_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    channels: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
