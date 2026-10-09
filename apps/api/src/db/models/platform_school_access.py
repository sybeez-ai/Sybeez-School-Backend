import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformSchoolAccess(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Grants a platform USER access to a specific school tenant.

    Admins/Super-admins implicitly have access to all schools, so this table
    is only used to expand what platform USERs can see/edit.
    """

    __tablename__ = "platform_school_access"
    __table_args__ = (UniqueConstraint("user_id", "school_id", name="uq_platform_school_access_user_school"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )

    user = relationship("User")
    school = relationship("School")

