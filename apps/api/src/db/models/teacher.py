import uuid
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import PersonStatus


class Teacher(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "teachers"
    __table_args__ = (UniqueConstraint("school_id", "user_id", name="uq_teacher_school_user"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=PersonStatus.ACTIVE.value, nullable=False)

    employee_number = synonym("employee_code")

    user = relationship("User")
    assignments = relationship("TeacherAssignment", back_populates="teacher", cascade="all, delete-orphan")
