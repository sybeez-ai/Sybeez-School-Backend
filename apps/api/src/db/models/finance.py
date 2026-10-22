"""Finance portal tables: branches, financial years, concessions, ledger, day close."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
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
from src.shared.enums import (
    ApprovalStatus,
    DayCloseStatus,
    FinancialYearStatus,
    InstrumentStatus,
    StudentFeeCategory,
)


class SchoolBranch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "school_branches"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_school_branch_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)


class FinancialYear(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "financial_years"
    __table_args__ = (UniqueConstraint("school_id", "label", name="uq_financial_year_label"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=FinancialYearStatus.OPEN.value)
    is_current: Mapped[bool] = mapped_column(nullable=False, default=False)


class SiblingGroup(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sibling_groups"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class StudentFeeProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "student_fee_profiles"
    __table_args__ = (UniqueConstraint("school_id", "student_id", name="uq_student_fee_profile"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StudentFeeCategory.GENERAL.value
    )
    sibling_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    transport_opt_in: Mapped[bool] = mapped_column(nullable=False, default=False)
    hostel_opt_in: Mapped[bool] = mapped_column(nullable=False, default=False)


class FinanceStaffScope(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Branch assignment for finance staff. No rows means the whole school."""

    __tablename__ = "finance_staff_scopes"
    __table_args__ = (
        UniqueConstraint("school_id", "user_id", "branch_id", name="uq_finance_staff_scope"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class FeeConcessionRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_concession_rules"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_fee_concession_rule_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fee_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    student_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class FeeConcessionRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_concession_requests"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    fee_category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    value_type: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    computed_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ApprovalStatus.PENDING.value)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeeWebhookEvent(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "fee_webhook_events"
    __table_args__ = (UniqueConstraint("event_id", name="uq_fee_webhook_event_id"),)

    school_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    credited: Mapped[bool] = mapped_column(nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeeRouteTransfer(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """School share of a Razorpay Route payment. One row per fee payment."""

    __tablename__ = "fee_route_transfers"
    __table_args__ = (UniqueConstraint("payment_id", name="uq_fee_route_transfer_payment"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    linked_account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_transfer_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    payment_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    transfer_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    platform_fee_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created", index=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    settlement_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FeePaymentTender(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_payment_tenders"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    instrument_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    instrument_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=InstrumentStatus.SETTLED.value)
    deposited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bounce_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    bounce_charge: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))


class FeeLedgerEntry(Base, UUIDPrimaryKeyMixin):
    """Append-only student fee ledger. Corrections are new reversal rows."""

    __tablename__ = "fee_ledger_entries"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    ref_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    narration: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FeeDayClose(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_day_closes"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    cashier_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    opening_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    physical_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_cheque: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_upi: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_card: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_neft: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_online: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    system_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    variance: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DayCloseStatus.SUBMITTED.value)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FinanceApproval(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "finance_approvals"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ApprovalStatus.PENDING.value)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class FineWaiverRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_fine_waiver_requests"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    invoice_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ApprovalStatus.PENDING.value)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExpenseHead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A school expense account, such as electricity or stationery."""

    __tablename__ = "expense_heads"
    __table_args__ = (
        Index(
            "uq_expense_heads_school_code",
            "school_id",
            text("lower(code)"),
            unique=True,
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SchoolExpense(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A vendor bill. GST is stored so the voucher can be filed."""

    __tablename__ = "school_expenses"
    __table_args__ = (
        UniqueConstraint("school_id", "voucher_no", name="uq_school_expenses_voucher"),
        CheckConstraint(
            "status IN ('DRAFT', 'SUBMITTED', 'APPROVED', 'REJECTED', 'PAID')",
            name="status_allowed",
        ),
        CheckConstraint("taxable_amount >= 0 AND gst_amount >= 0 AND total_amount >= 0", name="amounts_non_negative"),
        CheckConstraint("gst_rate >= 0 AND gst_rate <= 100", name="gst_rate_range"),
        CheckConstraint("status <> 'PAID' OR paid_on IS NOT NULL", name="paid_has_date"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    head_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    voucher_no: Mapped[str] = mapped_column(String(32), nullable=False)
    vendor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    taxable_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0"))
    gst_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="BANK")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT", index=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExpenseLedgerEntry(Base, UUIDPrimaryKeyMixin):
    """Append-only lines posted when an expense is approved and when it is paid."""

    __tablename__ = "expense_ledger_entries"

    school_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    expense_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    account: Mapped[str] = mapped_column(String(32), nullable=False)
    debit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    credit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    narration: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
