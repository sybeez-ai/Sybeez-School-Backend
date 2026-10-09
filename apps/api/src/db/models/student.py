import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import PersonStatus


class Student(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Student master profile — academic placement lives on enrollments."""

    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_student_school_user"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    admission_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    student_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(16), nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    admission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PersonStatus.ACTIVE.value, nullable=False)

    student_number = synonym("admission_no")

    user = relationship("User")
    enrollments = relationship("Enrollment", back_populates="student", cascade="all, delete-orphan")
    parent_links = relationship("StudentParent", back_populates="student", cascade="all, delete-orphan")
