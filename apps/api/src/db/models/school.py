from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import SchoolStatus


class School(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "schools"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=SchoolStatus.ACTIVE.value)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    memberships = relationship("SchoolMembership", back_populates="school", cascade="all, delete-orphan")
    subscriptions = relationship("Subscription", back_populates="school", cascade="all, delete-orphan")
    academic_years = relationship("AcademicYear", back_populates="school", cascade="all, delete-orphan")
