import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class BookstoreItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bookstore_items"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="textbooks")
    price_inr: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False, default=Decimal("0"))
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    badge: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class BookstoreWishlist(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bookstore_wishlists"
    __table_args__ = (UniqueConstraint("school_id", "student_id", name="uq_bookstore_wishlist_student"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class BookstoreOrder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bookstore_orders"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    items: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)

