import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FeeReminderLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_reminder_logs"
    __table_args__ = (
        UniqueConstraint("invoice_id", "kind", "sent_on", name="uq_fee_reminder_invoice_kind_day"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sent_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)

