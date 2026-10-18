"""Two-layer feature restriction: platform rules (global or per school) and school admin switches."""

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PlatformFeatureRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Set only by the platform super admin. ``school_id`` NULL = applies to every school."""

    __tablename__ = "platform_feature_rules"
    __table_args__ = (
        Index(
            "uq_platform_feature_rule",
            "feature_key",
            text("COALESCE(school_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
        ),
    )

    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pointer for the audit trail only (users are anonymised, never hard-deleted): no personal data stored here.
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class SchoolFeatureSetting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A school admin's own switch for one feature in their school."""

    __tablename__ = "school_feature_settings"
    __table_args__ = (Index("uq_school_feature_setting", "school_id", "feature_key", unique=True),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature_key: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
