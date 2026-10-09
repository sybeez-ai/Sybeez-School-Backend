import uuid

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ClassRoom(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A section within a grade for an academic year (e.g. section A of Class 5)."""

    __tablename__ = "classes"
    __table_args__ = (UniqueConstraint("school_id", "name", "academic_year_id", name="uq_class_school_name_year"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    section: Mapped[str | None] = mapped_column(String(16), nullable=True)
    capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    class_teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teachers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    grade = relationship("Grade", back_populates="classes")
    academic_year = relationship("AcademicYear", back_populates="classes")
    class_teacher = relationship("Teacher", foreign_keys=[class_teacher_id])
    enrollments = relationship("Enrollment", back_populates="classroom")
    teacher_assignments = relationship("TeacherAssignment", back_populates="classroom")
    timetable_entries = relationship("TimetableEntry", back_populates="classroom")
