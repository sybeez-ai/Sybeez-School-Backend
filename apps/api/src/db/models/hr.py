import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

LEAVE_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED")
PAYSLIP_STATUSES = ("DRAFT", "PAID", "CANCELLED")
STAFF_TYPES = ("TEACHING", "NON_TEACHING")
EMPLOYEE_STATUSES = ("ACTIVE", "INACTIVE", "LEFT")
EMPLOYMENT_TYPES = ("FULL_TIME", "PART_TIME", "CONTRACT", "TEMPORARY")
STAFF_ATTENDANCE_STATUSES = ("PRESENT", "ABSENT", "HALF_DAY", "LEAVE")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Department(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_departments_id_school_id"),
        Index("uq_departments_school_name", "school_id", text("lower(name)"), unique=True),
        ForeignKeyConstraint(
            ["head_employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_departments_head_same_school",
            ondelete="SET NULL (head_employee_id)",
            use_alter=True,
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Designation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "designations"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_designations_id_school_id"),
        Index("uq_designations_school_name", "school_id", text("lower(name)"), unique=True),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Employee(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """HR record for every staff member of a school.

    TEACHING rows point at ``teachers``; that row and its user stay the source of truth for
    identity, contact and active status. NON_TEACHING rows hold those fields here. HR-only
    metadata lives here for both kinds.
    """

    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_employees_id_school_id"),
        UniqueConstraint("teacher_id", name="uq_employees_teacher_id"),
        ForeignKeyConstraint(
            ["teacher_id", "school_id"],
            ["teachers.id", "teachers.school_id"],
            name="fk_employees_teacher_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("staff_type", STAFF_TYPES), name="staff_type_allowed"),
        CheckConstraint("(staff_type = 'TEACHING') = (teacher_id IS NOT NULL)", name="teacher_link_matches_type"),
        CheckConstraint("staff_type = 'TEACHING' OR full_name IS NOT NULL", name="non_teaching_has_name"),
        CheckConstraint(_in("status", EMPLOYEE_STATUSES), name="status_allowed"),
        CheckConstraint(
            f"employment_type IS NULL OR {_in('employment_type', EMPLOYMENT_TYPES)}", name="employment_type_allowed"
        ),
        ForeignKeyConstraint(
            ["department_id", "school_id"],
            ["departments.id", "departments.school_id"],
            name="fk_employees_department_same_school",
            ondelete="SET NULL (department_id)",
        ),
        ForeignKeyConstraint(
            ["designation_id", "school_id"],
            ["designations.id", "designations.school_id"],
            name="fk_employees_designation_same_school",
            ondelete="SET NULL (designation_id)",
        ),
        Index(
            "uq_employees_school_employee_code",
            "school_id",
            text("lower(employee_code)"),
            unique=True,
            postgresql_where=text("employee_code IS NOT NULL"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    staff_type: Mapped[str] = mapped_column(String(16), nullable=False)
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employee_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    qualification: Mapped[str | None] = mapped_column(String(255), nullable=True)
    photo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)

    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    designation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    employment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    emergency_contact_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    emergency_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class StaffAttendance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One attendance mark per employee per day."""

    __tablename__ = "staff_attendance"
    __table_args__ = (
        UniqueConstraint("employee_id", "attendance_date", name="uq_staff_attendance_employee_date"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_staff_attendance_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", STAFF_ATTENDANCE_STATUSES), name="status_allowed"),
        Index("ix_staff_attendance_school_date", "school_id", "attendance_date"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    marked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class StaffLeaveRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_leave_requests"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="date_order"),
        CheckConstraint(_in("status", LEAVE_STATUSES), name="status_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_runs"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_payroll_runs_id_school_id"),
        CheckConstraint("period_end >= period_start", name="period_order"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PayrollPayslip(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_payslips"
    __table_args__ = (
        ForeignKeyConstraint(
            ["payroll_run_id", "school_id"],
            ["payroll_runs.id", "payroll_runs.school_id"],
            name="fk_payroll_payslips_run_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["teacher_id", "school_id"],
            ["teachers.id", "teachers.school_id"],
            name="fk_payroll_payslips_teacher_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint("gross_amount >= 0", name="gross_non_negative"),
        CheckConstraint("deductions_amount >= 0", name="deductions_non_negative"),
        CheckConstraint("net_amount >= 0", name="net_non_negative"),
        CheckConstraint(_in("status", PAYSLIP_STATUSES), name="status_allowed"),
        CheckConstraint("status <> 'PAID' OR paid_on IS NOT NULL", name="paid_has_date"),
        Index(
            "uq_payroll_payslips_run_teacher_active",
            "payroll_run_id",
            "teacher_id",
            unique=True,
            postgresql_where=text("status <> 'CANCELLED'"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payroll_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), nullable=False, index=True
    )

    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    deductions_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

