import uuid

from sqlalchemy import Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Result(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "results"
    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_result_exam_student"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    exam = relationship("Exam", back_populates="results")
