import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StudentParent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Many-to-many: students ↔ parents (one parent, many children)."""

    __tablename__ = "student_parents"
    __table_args__ = (UniqueConstraint("student_id", "parent_id", name="uq_student_parent"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # DB column "relationship" — attribute cannot be named relationship (SQLAlchemy API clash)
    relation_type: Mapped[str] = mapped_column("relationship", String(64), default="guardian", nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    student = relationship("Student", back_populates="parent_links")
    parent = relationship("Parent", back_populates="student_links")
