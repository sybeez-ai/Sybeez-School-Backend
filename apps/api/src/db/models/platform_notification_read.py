import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base


class PlatformNotificationRead(Base):
    """Read state of the platform console bell for one platform user.

    Platform notifications are derived from audit_logs (new schools, school admins added,
    login failures, app errors); only what this user has read is stored here.
    """

    __tablename__ = "platform_notification_reads"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    read_all_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
