import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformStaff(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Internal employee invited by the super admin. Not a school membership."""

    __tablename__ = "platform_staff"
    __table_args__ = (UniqueConstraint("user_id", name="uq_platform_staff_user"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PlatformStaffPortal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_staff_portals"
    __table_args__ = (UniqueConstraint("staff_id", "portal", name="uq_platform_staff_portal"),)

    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    portal: Mapped[str] = mapped_column(String(32), nullable=False)


class PlatformStaffPermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Extra feature permission on top of the portal bundles."""

    __tablename__ = "platform_staff_permissions"
    __table_args__ = (UniqueConstraint("staff_id", "permission_code", name="uq_platform_staff_permission"),)

    staff_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_staff.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_code: Mapped[str] = mapped_column(String(64), nullable=False)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PlatformStaffInvitation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "platform_staff_invitations"

    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    portals: Mapped[list] = mapped_column(JSONB, nullable=False)
    permissions: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
