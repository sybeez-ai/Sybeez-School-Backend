import uuid

from sqlalchemy import Float, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class StudentProgress(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Official school-authoritative progress (not AI estimates)."""

    __tablename__ = "student_progress"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "subject_id",
            "academic_year_id",
            "metric",
            name="uq_student_progress_metric",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=True, index=True
    )
    metric: Mapped[str] = mapped_column(String(64), nullable=False, default="percentage")
    value: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


# Back-compat alias
Progress = StudentProgress
