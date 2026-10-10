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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

LEAVE_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED")
PAYSLIP_STATUSES = ("DRAFT", "PUBLISHED", "PAID", "CANCELLED")
STAFF_TYPES = ("TEACHING", "NON_TEACHING")
EMPLOYEE_STATUSES = ("ACTIVE", "INACTIVE", "LEFT")
EMPLOYMENT_TYPES = ("FULL_TIME", "PART_TIME", "CONTRACT", "TEMPORARY")
STAFF_ATTENDANCE_STATUSES = ("PRESENT", "ABSENT", "HALF_DAY", "LEAVE")
LETTER_TYPES = ("OFFER", "APPOINTMENT", "EXPERIENCE", "RELIEVING", "OTHER")
LETTER_STATUSES = ("DRAFT", "ISSUED", "REVOKED")
EMPLOYEE_DOCUMENT_CATEGORIES = ("ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "OTHER")
EMPLOYEE_DOCUMENT_STATUSES = ("ACTIVE", "ARCHIVED")
WORKFLOW_TYPES = ("ONBOARDING", "OFFBOARDING")
WORKFLOW_STATUSES = ("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED")
WORKFLOW_ACTIVE_STATUSES = ("OPEN", "IN_PROGRESS")
HR_REQUEST_TYPES = ("PROFILE_UPDATE", "DOCUMENT_REQUEST", "LETTER_REQUEST", "HR_QUERY", "OTHER")
HR_REQUEST_STATUSES = ("PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "RESOLVED", "CANCELLED")
HR_REQUEST_OPEN_STATUSES = ("PENDING", "IN_REVIEW")
HR_REQUEST_DECIDED_STATUSES = ("APPROVED", "REJECTED", "RESOLVED")
HR_REQUEST_SOURCES = ("EMPLOYEE", "ADMIN")
HR_REQUEST_EVENTS = ("SUBMITTED", "STATUS_CHANGED", "COMMENT", "CANCELLED")


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


class HrLetter(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An HR letter for one employee. Content is frozen once issued."""

    __tablename__ = "hr_letters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_letters_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("letter_type", LETTER_TYPES), name="type_allowed"),
        CheckConstraint(_in("status", LETTER_STATUSES), name="status_allowed"),
        CheckConstraint("status = 'DRAFT' OR (issue_date IS NOT NULL AND issued_at IS NOT NULL)", name="issued_has_date"),
        Index(
            "uq_hr_letters_school_reference",
            "school_id",
            "reference_number",
            unique=True,
            postgresql_where=text("reference_number IS NOT NULL"),
        ),
        Index("ix_hr_letters_school_employee", "school_id", "employee_id"),
        Index("ix_hr_letters_school_status", "school_id", "status"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    letter_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    reference_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class EmployeeDocument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A private HR file for one employee. Bytes live outside the public media mount."""

    __tablename__ = "employee_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_employee_documents_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("category", EMPLOYEE_DOCUMENT_CATEGORIES), name="category_allowed"),
        CheckConstraint(_in("status", EMPLOYEE_DOCUMENT_STATUSES), name="status_allowed"),
        CheckConstraint("file_size > 0", name="file_size_positive"),
        UniqueConstraint("storage_key", name="uq_employee_documents_storage_key"),
        Index("ix_employee_documents_school_employee", "school_id", "employee_id"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    shared_with_employee: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HrWorkflow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An onboarding or offboarding journey for one employee, tracked through checklist tasks."""

    __tablename__ = "hr_workflows"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_workflows_id_school_id"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_workflows_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("workflow_type", WORKFLOW_TYPES), name="type_allowed"),
        CheckConstraint(_in("status", WORKFLOW_STATUSES), name="status_allowed"),
        CheckConstraint(
            "expected_completion_date IS NULL OR expected_completion_date >= start_date",
            name="expected_after_start",
        ),
        CheckConstraint("status <> 'COMPLETED' OR completed_at IS NOT NULL", name="completed_has_time"),
        CheckConstraint("status <> 'CANCELLED' OR cancelled_at IS NOT NULL", name="cancelled_has_time"),
        Index(
            "uq_hr_workflows_one_active_per_type",
            "school_id",
            "employee_id",
            "workflow_type",
            unique=True,
            postgresql_where=text("status IN ('OPEN', 'IN_PROGRESS')"),
        ),
        Index("ix_hr_workflows_school_status", "school_id", "status"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrWorkflowTask(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_workflow_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "school_id"],
            ["hr_workflows.id", "hr_workflows.school_id"],
            name="fk_hr_workflow_tasks_workflow_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint("NOT is_done OR completed_at IS NOT NULL", name="done_has_time"),
        Index("ix_hr_workflow_tasks_workflow_order", "workflow_id", "sort_order"),
        Index("ix_hr_workflow_tasks_assignee", "school_id", "assignee_user_id"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An employee self-service request to HR (never payroll or finance)."""

    __tablename__ = "hr_requests"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_requests_id_school_id"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_requests_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("request_type", HR_REQUEST_TYPES), name="type_allowed"),
        CheckConstraint(_in("status", HR_REQUEST_STATUSES), name="status_allowed"),
        CheckConstraint(_in("source", HR_REQUEST_SOURCES), name="source_allowed"),
        CheckConstraint(
            f"NOT ({_in('status', HR_REQUEST_DECIDED_STATUSES)}) OR decided_at IS NOT NULL",
            name="decided_has_time",
        ),
        Index("ix_hr_requests_school_status", "school_id", "status"),
        Index("ix_hr_requests_school_employee", "school_id", "employee_id"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requested_changes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="EMPLOYEE")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class HrRequestEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only history for an HR request: submission, status changes, comments."""

    __tablename__ = "hr_request_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["request_id", "school_id"],
            ["hr_requests.id", "hr_requests.school_id"],
            name="fk_hr_request_events_request_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("event_type", HR_REQUEST_EVENTS), name="event_allowed"),
        Index("ix_hr_request_events_request", "request_id", "created_at"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-school HR business rules (one row per school; defaults apply until an admin saves)."""

    __tablename__ = "hr_settings"
    __table_args__ = (
        UniqueConstraint("school_id", name="uq_hr_settings_school"),
        CheckConstraint("letter_reference_prefix ~ '^[A-Z0-9]{1,10}$'", name="letter_prefix_format"),
        CheckConstraint("leave_max_days BETWEEN 1 AND 366", name="leave_max_days_range"),
        CheckConstraint("onboarding_target_days BETWEEN 0 AND 365", name="onboarding_target_range"),
        CheckConstraint("jsonb_typeof(required_document_categories) = 'array'", name="required_docs_array"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    letter_reference_prefix: Mapped[str] = mapped_column(String(10), nullable=False, default="HR")
    required_document_categories: Mapped[list] = mapped_column(JSONB, nullable=False)
    leave_max_days: Mapped[int] = mapped_column(Integer, nullable=False, default=366)
    onboarding_target_days: Mapped[int] = mapped_column(Integer, nullable=False, default=14)
    notify_leave: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_requests: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_letters: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notify_onboarding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
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
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
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
    run_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    approval_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="MANUAL")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    teacher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teachers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    deductions_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0.00"))

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT", index=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    lop_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    paid_days: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    pdf_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tax_regime: Mapped[str | None] = mapped_column(String(8), nullable=True)
    form16: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    download_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    first_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

