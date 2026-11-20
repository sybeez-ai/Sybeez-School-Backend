"""Library, activities, and the school calendar. One school, optional section."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class LibraryTitle(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "library_titles"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    author: Mapped[str | None] = mapped_column(String(160), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(80), nullable=True)


class LibraryCopy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "library_copies"
    __table_args__ = (UniqueConstraint("school_id", "barcode", name="uq_library_copies_barcode"),)

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    title_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("library_titles.id", ondelete="CASCADE"), nullable=False, index=True)
    barcode: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="AVAILABLE")  # AVAILABLE LOANED LOST


class LibraryLoan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "library_loans"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    copy_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("library_copies.id", ondelete="CASCADE"), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    returned_on: Mapped[date | None] = mapped_column(Date, nullable=True)


class LibraryFine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "library_fines"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    loan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("library_loans.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")  # OPEN PAID WAIVED
    note: Mapped[str | None] = mapped_column(String(240), nullable=True)


class SchoolActivity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_activities"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVITY")  # ACTIVITY EVENT
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ends_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    place: Mapped[str | None] = mapped_column(String(160), nullable=True)


class SchoolCalendarItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_calendar_items"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True)
    class_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # HOLIDAY TERM EXAM EVENT
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
