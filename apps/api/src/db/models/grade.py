import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Grade(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("school_id", "name", name="uq_grade_school_name"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    display_order = synonym("level")

    classes = relationship("ClassRoom", back_populates="grade")
    enrollments = relationship("Enrollment", back_populates="grade")
