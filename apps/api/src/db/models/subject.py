import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import SubjectStatus


class Subject(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "subjects"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_subject_school_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=SubjectStatus.ACTIVE.value, nullable=False)

    teacher_assignments = relationship("TeacherAssignment", back_populates="subject")
    syllabus_entries = relationship("Syllabus", back_populates="subject")
