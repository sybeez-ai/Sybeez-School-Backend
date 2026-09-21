import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import EnrollmentStatus


class Enrollment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Student ↔ class for an academic year (preserves history across years)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year_id", name="uq_enrollment_student_year"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grades.id", ondelete="SET NULL"), nullable=True, index=True
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=EnrollmentStatus.ACTIVE.value, nullable=False)
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    student = relationship("Student", back_populates="enrollments")
    classroom = relationship("ClassRoom", back_populates="enrollments")
    academic_year = relationship("AcademicYear", back_populates="enrollments")
    grade = relationship("Grade", back_populates="enrollments")
