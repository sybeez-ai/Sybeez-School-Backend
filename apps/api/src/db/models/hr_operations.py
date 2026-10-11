"""HR operations added after the 0052 head: reports, probation, appraisals, recruitment,
training, rostering, assets, and exit settlement."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

REPORT_KINDS = ("HEADCOUNT", "ATTENDANCE", "LEAVE", "ATTRITION", "DEPARTMENT", "STATUTORY")
REPORT_FORMATS = ("JSON", "CSV", "XLSX", "PDF")
PROBATION_STATUSES = ("ACTIVE", "EXTENDED", "CONFIRMED", "TERMINATED")
CYCLE_STATUSES = ("DRAFT", "OPEN", "CLOSED")
GOAL_STATUSES = ("OPEN", "DONE")
APPRAISAL_STATUSES = ("OPEN", "SELF_SUBMITTED", "MANAGER_REVIEWED", "FINAL")
REVIEW_KINDS = ("SELF", "MANAGER")
OPENING_STATUSES = ("DRAFT", "OPEN", "CLOSED")
APPLICATION_STATUSES = ("APPLIED", "SCREENING", "INTERVIEW", "OFFERED", "HIRED", "REJECTED", "WITHDRAWN")
INTERVIEW_STAGES = ("SCREENING", "TECHNICAL", "HR", "FINAL")
INTERVIEW_OUTCOMES = ("SCHEDULED", "PASSED", "FAILED", "NO_SHOW")
OFFER_STATUSES = ("DRAFT", "SENT", "ACCEPTED", "DECLINED")
TRAINING_STATUSES = ("ASSIGNED", "COMPLETED", "EXPIRED")
ROSTER_DUTIES = ("TEACHING", "DUTY", "OFF")
ASSET_STATUSES = ("IN_STOCK", "ISSUED", "RETURNED", "WRITTEN_OFF")
EXIT_CASE_STATUSES = ("DRAFT", "POSTED")
SETTLEMENT_LINE_KINDS = ("DUE", "RECOVERY")


def _in(column: str, values: tuple[str, ...]) -> str:
    listed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({listed})"


class HrReportExport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One generated HR report. The file itself is not stored; the audit row is."""

    __tablename__ = "hr_report_exports"
    __table_args__ = (
        CheckConstraint(_in("kind", REPORT_KINDS), name="kind_allowed"),
        CheckConstraint(_in("format", REPORT_FORMATS), name="format_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrProbation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_probations"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_probations_id_school_id"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_probations_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", PROBATION_STATUSES), name="status_allowed"),
        CheckConstraint("end_date >= start_date", name="end_after_start"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    extended_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrAppraisalCycle(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_appraisal_cycles"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_appraisal_cycles_id_school_id"),
        CheckConstraint(_in("status", CYCLE_STATUSES), name="status_allowed"),
        CheckConstraint("period_end >= period_start", name="period_order"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrAppraisalGoal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_appraisal_goals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cycle_id", "school_id"],
            ["hr_appraisal_cycles.id", "hr_appraisal_cycles.school_id"],
            name="fk_hr_appraisal_goals_cycle_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_appraisal_goals_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", GOAL_STATUSES), name="status_allowed"),
        CheckConstraint("weight >= 0 AND weight <= 100", name="weight_range"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")


class HrAppraisal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_appraisals"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_appraisals_id_school_id"),
        UniqueConstraint("cycle_id", "employee_id", name="uq_hr_appraisals_cycle_employee"),
        ForeignKeyConstraint(
            ["cycle_id", "school_id"],
            ["hr_appraisal_cycles.id", "hr_appraisal_cycles.school_id"],
            name="fk_hr_appraisals_cycle_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_appraisals_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", APPRAISAL_STATUSES), name="status_allowed"),
        CheckConstraint("final_rating IS NULL OR (final_rating >= 1 AND final_rating <= 5)", name="rating_range"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="OPEN")
    final_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 1), nullable=True)
    final_comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrAppraisalReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_appraisal_reviews"
    __table_args__ = (
        UniqueConstraint("appraisal_id", "kind", name="uq_hr_appraisal_reviews_appraisal_kind"),
        ForeignKeyConstraint(
            ["appraisal_id", "school_id"],
            ["hr_appraisals.id", "hr_appraisals.school_id"],
            name="fk_hr_appraisal_reviews_appraisal_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("kind", REVIEW_KINDS), name="kind_allowed"),
        CheckConstraint("rating >= 1 AND rating <= 5", name="rating_range"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    appraisal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewer_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 1), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrJobOpening(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_job_openings"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_job_openings_id_school_id"),
        CheckConstraint(_in("status", OPENING_STATUSES), name="status_allowed"),
        CheckConstraint("openings >= 1", name="openings_positive"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    designation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    openings: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrApplication(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_applications"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_applications_id_school_id"),
        ForeignKeyConstraint(
            ["opening_id", "school_id"],
            ["hr_job_openings.id", "hr_job_openings.school_id"],
            name="fk_hr_applications_opening_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", APPLICATION_STATUSES), name="status_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opening_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    applicant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="APPLIED")
    employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrInterview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_interviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "school_id"],
            ["hr_applications.id", "hr_applications.school_id"],
            name="fk_hr_interviews_application_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("stage", INTERVIEW_STAGES), name="stage_allowed"),
        CheckConstraint(_in("outcome", INTERVIEW_OUTCOMES), name="outcome_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    interviewer_employee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="SCHEDULED")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrOffer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_offers"
    __table_args__ = (
        UniqueConstraint("application_id", name="uq_hr_offers_application_id"),
        ForeignKeyConstraint(
            ["application_id", "school_id"],
            ["hr_applications.id", "hr_applications.school_id"],
            name="fk_hr_offers_application_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", OFFER_STATUSES), name="status_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    offered_on: Mapped[date] = mapped_column(Date, nullable=False)
    joining_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    salary_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")


class HrTrainingProgram(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_training_programs"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_training_programs_id_school_id"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(160), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_months: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HrTrainingRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_training_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["program_id", "school_id"],
            ["hr_training_programs.id", "hr_training_programs.school_id"],
            name="fk_hr_training_records_program_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_training_records_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", TRAINING_STATUSES), name="status_allowed"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    program_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    certificate_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ASSIGNED")


class HrShift(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_shifts"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_shifts_id_school_id"),
        CheckConstraint("break_minutes >= 0", name="break_non_negative"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class HrRosterEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_roster_entries"
    __table_args__ = (
        UniqueConstraint("employee_id", "duty_date", "shift_id", name="uq_hr_roster_entries_employee_day_shift"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_roster_entries_employee_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["shift_id", "school_id"],
            ["hr_shifts.id", "hr_shifts.school_id"],
            name="fk_hr_roster_entries_shift_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("duty", ROSTER_DUTIES), name="duty_allowed"),
        CheckConstraint("overtime_minutes >= 0", name="overtime_non_negative"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    duty_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    duty: Mapped[str] = mapped_column(String(16), nullable=False, default="DUTY")
    timetable_period: Mapped[str | None] = mapped_column(String(40), nullable=True)
    overtime_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrAsset(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_assets"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_assets_id_school_id"),
        UniqueConstraint("school_id", "asset_tag", name="uq_hr_assets_school_tag"),
        CheckConstraint(_in("status", ASSET_STATUSES), name="status_allowed"),
        CheckConstraint("cost >= 0", name="cost_non_negative"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    serial_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_STOCK")


class HrAssetIssue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_asset_issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["asset_id", "school_id"],
            ["hr_assets.id", "hr_assets.school_id"],
            name="fk_hr_asset_issues_asset_same_school",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_asset_issues_employee_same_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    asset_name: Mapped[str] = mapped_column(String(160), nullable=False)
    asset_tag: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    returned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    condition_out: Mapped[str | None] = mapped_column(String(80), nullable=True)
    condition_in: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HrExitCase(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Full-and-final settlement for one leaver, posted into payroll."""

    __tablename__ = "hr_exit_cases"
    __table_args__ = (
        UniqueConstraint("id", "school_id", name="uq_hr_exit_cases_id_school_id"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_hr_exit_cases_employee_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("status", EXIT_CASE_STATUSES), name="status_allowed"),
        CheckConstraint("dues_total >= 0 AND recoveries_total >= 0 AND payable >= 0", name="amounts_non_negative"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    exit_date: Mapped[date] = mapped_column(Date, nullable=False)
    exit_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    dues_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    recoveries_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    payable: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    payroll_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payslip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payroll_settlement_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class HrSettlementLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "hr_settlement_lines"
    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "school_id"],
            ["hr_exit_cases.id", "hr_exit_cases.school_id"],
            name="fk_hr_settlement_lines_case_same_school",
            ondelete="CASCADE",
        ),
        CheckConstraint(_in("kind", SETTLEMENT_LINE_KINDS), name="kind_allowed"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
