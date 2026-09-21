import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import PersonStatus


class Student(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Student profile — identity is users; membership role is school_memberships."""

    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_student_school_user"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admission_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PersonStatus.ACTIVE.value, nullable=False)

    student_number = synonym("admission_no")

    user = relationship("User")
    enrollments = relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")
    parent_links = relationship("StudentParent", back_populates="student", cascade="all, delete-orphan")
