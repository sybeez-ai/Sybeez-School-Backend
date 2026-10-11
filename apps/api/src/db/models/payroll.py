"""Production payroll tables for Sybeez School.

Every tenant table carries school_id. Row level security is applied in migration 0040.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
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
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class PayrollSchoolSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_school_settings"
    __table_args__ = (UniqueConstraint("school_id", name="uq_payroll_settings_school"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    pt_state: Mapped[str] = mapped_column(String(8), nullable=False, default="KA")
    tax_regime_default: Mapped[str] = mapped_column(String(8), nullable=False, default="NEW")
    pf_employee_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("12"))
    pf_employer_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("12"))
    pf_wage_ceiling: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("15000"))
    pf_default_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="CAPPED")
    eps_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("8.33"))
    esi_employee_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0.75"))
    esi_employer_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("3.25"))
    esi_wage_ceiling: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("21000"))
    rounding_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="NEAREST_RUPEE")
    lop_base: Mapped[str] = mapped_column(String(16), nullable=False, default="BASIC")
    pay_date_rule: Mapped[str] = mapped_column(String(32), nullable=False, default="LAST_WORKING_DAY")
    pay_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bank_format: Mapped[str] = mapped_column(String(32), nullable=False, default="GENERIC_NEFT")
    template_code: Mapped[str] = mapped_column(String(32), nullable=False, default="CLASSIC")
    primary_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#1B4F72")
    accent_color: Mapped[str] = mapped_column(String(7), nullable=False, default="#148F77")
    logo_object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    show_employer_share: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_lop: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_bank_last4: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_pan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    shift_start: Mapped[time] = mapped_column(Time, nullable=False, default=time(9, 0))
    shift_end: Mapped[time] = mapped_column(Time, nullable=False, default=time(16, 0))
    grace_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    full_day_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=360)
    half_day_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=180)
    geofence_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    geofence_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    geofence_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    geofence_radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    bonus_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bonus_percent: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("8.33"))
    bonus_wage_ceiling: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("21000"))
    bonus_calc_ceiling: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("7000"))
    bonus_month: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    gratuity_cap: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("2000000"))
    gratuity_min_years: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False, default=Decimal("5"))
    require_different_approver: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class PayrollSettingRevision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_setting_revisions"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class SalaryComponent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "salary_components"
    __table_args__ = (
        Index(
            "uq_salary_components_school_code_open",
            "school_id",
            text("lower(code)"),
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    calc_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)
    percent_of: Mapped[str | None] = mapped_column(String(32), nullable=True)
    formula: Mapped[str | None] = mapped_column(String(200), nullable=True)
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pf_wage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    esi_wage: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    prorate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class SalaryTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "salary_templates"
    __table_args__ = (
        Index("uq_salary_templates_school_name", "school_id", text("lower(name)"), unique=True),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    grade_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SalaryTemplateLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "salary_template_lines"
    __table_args__ = (UniqueConstraint("template_id", "component_id", name="uq_template_line_component"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("salary_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("salary_components.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    percent: Mapped[Decimal | None] = mapped_column(Numeric(8, 4), nullable=True)


class EmployeeSalaryAssignment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "employee_salary_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_salary_assignment_employee_school",
            ondelete="CASCADE",
        ),
        Index("ix_salary_assignment_employee_from", "employee_id", "effective_from"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("salary_templates.id", ondelete="RESTRICT"), nullable=False
    )
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmployeeStatutoryProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "employee_statutory_profiles"
    __table_args__ = (
        UniqueConstraint("employee_id", name="uq_statutory_employee"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_statutory_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    pan: Mapped[str | None] = mapped_column(String(10), nullable=True)
    uan: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pf_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    esi_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gender: Mapped[str] = mapped_column(String(8), nullable=False, default="OTHER")
    tax_regime: Mapped[str | None] = mapped_column(String(8), nullable=True)
    metro: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pt_state: Mapped[str | None] = mapped_column(String(8), nullable=True)
    pf_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pf_wage_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pf_employee_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    pf_employer_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    pf_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    pf_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    vpf_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    vpf_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    esi_enrolled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    esi_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    esi_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class EmployeeBankAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "employee_bank_accounts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_bank_employee_school",
            ondelete="CASCADE",
        ),
        Index(
            "uq_employee_bank_primary",
            "employee_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    account_holder: Mapped[str] = mapped_column(String(120), nullable=False)
    account_number: Mapped[str] = mapped_column(String(18), nullable=False)
    ifsc: Mapped[str] = mapped_column(String(11), nullable=False)
    bank_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SchoolHoliday(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_holidays"
    __table_args__ = (
        Index("ix_school_holidays_school_day", "school_id", "holiday_date"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    branch_name: Mapped[str | None] = mapped_column(String(120), nullable=True)


class WeeklyOffRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "weekly_off_rules"
    __table_args__ = (
        UniqueConstraint("school_id", "weekday", "occurrence", "branch_name", name="uq_weekly_off"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    occurrence: Mapped[str] = mapped_column(String(8), nullable=False, default="ALL")
    branch_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")


class PtSlabRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "pt_slab_rules"

    school_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=True, index=True
    )
    state_code: Mapped[str] = mapped_column(String(8), nullable=False)
    gender: Mapped[str] = mapped_column(String(8), nullable=False, default="ANY")
    min_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    february_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="MONTHLY")
    basis: Mapped[str] = mapped_column(String(24), nullable=False, default="MONTHLY_GROSS")


class PayrollLoan(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_loans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_loan_employee_school",
            ondelete="CASCADE",
        ),
        CheckConstraint("principal >= 0 AND balance >= 0 AND emi >= 0", name="loan_amounts"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="LOAN")
    principal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    emi: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    annual_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0"))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    paused_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")


class PayrollLoanEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One EMI, prepayment, closure, or reversal against a loan."""

    __tablename__ = "payroll_loan_events"
    __table_args__ = (
        CheckConstraint("principal >= 0 AND interest >= 0 AND balance_after >= 0", name="loan_event_amounts"),
        CheckConstraint(_in("kind", ("EMI", "PREPAY", "CLOSE", "REVERSAL")), name="loan_event_kind"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_loans.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payslip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    principal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    interest: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollArrear(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_arrears"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_arrear_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    payslip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class PayrollReimbursement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_reimbursements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_reimburse_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    payslip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class TaxDeclaration(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tax_declarations"
    __table_args__ = (
        UniqueConstraint("employee_id", "financial_year", name="uq_declaration_employee_fy"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_declaration_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    financial_year: Mapped[str] = mapped_column(String(9), nullable=False)
    regime: Mapped[str] = mapped_column(String(8), nullable=False, default="NEW")
    section_80c: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    section_80d: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    other_chapter_vi: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    annual_rent: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")


class TaxDeclarationProof(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tax_declaration_proofs"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    declaration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tax_declarations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    object_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollPayslipLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_payslip_lines"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payslip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_payslips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class PayrollPayment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_payments"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payslip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_payslips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    utr: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollAssistantAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A write the assistant proposed. It runs only after the user confirms it."""

    __tablename__ = "payroll_assistant_actions"
    __table_args__ = (
        CheckConstraint(_in("status", ("PROPOSED", "EXECUTED")), name="assistant_action_status"),
        Index(
            "uq_payroll_assistant_idempotency",
            "school_id",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PROPOSED")
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PayrollAssistantImport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A parsed pay sheet. Payroll rows are written only after confirm."""

    __tablename__ = "payroll_assistant_imports"
    __table_args__ = (
        CheckConstraint(_in("status", ("PREVIEW", "IMPORTED")), name="assistant_import_status"),
        Index(
            "uq_payroll_assistant_import_idempotency",
            "school_id",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False, default="application/octet-stream")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PREVIEW")
    mapping: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preview: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class PayrollOpeningYtd(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Figures already paid this financial year before the school started on this payroll."""

    __tablename__ = "payroll_opening_ytd"
    __table_args__ = (
        UniqueConstraint("employee_id", "financial_year", name="uq_opening_ytd_employee_fy"),
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_opening_ytd_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    financial_year: Mapped[str] = mapped_column(String(9), nullable=False)
    ytd_gross: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    ytd_tds: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    ytd_pf: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    ytd_esi: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    ytd_pt: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="IMPORT")


class PayrollAuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "payroll_audit_logs"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PayrollSettlement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_settlements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_settlement_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    payroll_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_runs.id", ondelete="SET NULL"), nullable=True
    )
    last_working_day: Mapped[date] = mapped_column(Date, nullable=False)
    gratuity: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    leave_encashment: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    notice_recovery: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    net: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_APPROVAL")
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ComplianceEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_compliance_events"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class PayrollRoleGrant(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "payroll_role_grants"
    __table_args__ = (
        UniqueConstraint("school_id", "user_id", "role", name="uq_payroll_grant"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class StaffAttendanceSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_attendance_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_staff_session_employee_school",
            ondelete="CASCADE",
        ),
        Index("ix_staff_session_school_day", "school_id", "attendance_date"),
        Index(
            "uq_staff_session_open",
            "employee_id",
            unique=True,
            postgresql_where=text("check_out_at IS NULL"),
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    server_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    check_in_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    check_in_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    check_out_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    check_out_lng: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="SELF")
    geofence_status: Mapped[str] = mapped_column(String(16), nullable=False, default="DISABLED")
    manual_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_late: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    left_early: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    derived_status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    flagged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    corrected_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class StaffAttendanceRegularisation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_attendance_regularisations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["employee_id", "school_id"],
            ["employees.id", "employees.school_id"],
            name="fk_regularisation_employee_school",
            ondelete="CASCADE",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    attendance_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_check_out: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class StaffAttendancePeriodLock(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "staff_attendance_period_locks"
    __table_args__ = (
        UniqueConstraint("school_id", "period_start", "period_end", name="uq_attendance_period_lock"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    payroll_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payroll_runs.id", ondelete="SET NULL"), nullable=True
    )
