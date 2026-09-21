import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Syllabus(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Subject syllabus for a grade within an academic year."""

    __tablename__ = "syllabus"
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "academic_year_id",
            "grade_id",
            "subject_id",
            name="uq_syllabus_year_grade_subject",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    grade_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grades.id", ondelete="CASCADE"), nullable=False, index=True
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    subject = relationship("Subject", back_populates="syllabus_entries")
    topics = relationship(
        "SyllabusTopic",
        back_populates="syllabus",
        cascade="all, delete-orphan",
        order_by="SyllabusTopic.order_index",
    )


class SyllabusTopic(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hierarchical syllabus topics (e.g. Light → Reflection)."""

    __tablename__ = "syllabus_topics"

    syllabus_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syllabus.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_topic_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("syllabus_topics.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    syllabus = relationship("Syllabus", back_populates="topics")
    parent = relationship("SyllabusTopic", remote_side="SyllabusTopic.id", backref="children")
