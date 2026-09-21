import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import PersonStatus


class Parent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "parents"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_parent_school_user"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PersonStatus.ACTIVE.value, nullable=False)

    user = relationship("User")
    student_links = relationship("StudentParent", back_populates="parent", cascade="all, delete-orphan")
