import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import MembershipStatus, UserRole


class SchoolMembership(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_memberships"
    __table_args__ = (UniqueConstraint("user_id", "school_id", "role", name="uq_membership_user_school_role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.STUDENT.value)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=MembershipStatus.ACTIVE.value)

    user = relationship("User", back_populates="memberships")
    school = relationship("School", back_populates="memberships")
