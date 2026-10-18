"""Phase 10 fee & payment models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from src.shared.enums import (
    FeeAdjustmentType,
    FeeChargeModel,
    FeeInvoiceStatus,
    FeeStructureStatus,
    PaymentMethod,
    PaymentStatus,
    RefundStatus,
    StudentFeeAccountStatus,
)


class FeeCategory(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_categories"
    __table_args__ = (UniqueConstraint("school_id", "code", name="uq_fee_category_school_code"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_80c_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0"))
    tally_ledger: Mapped[str | None] = mapped_column(String(128), nullable=True)


class FeeStructure(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_structures"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    grade_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("grades.id", ondelete="SET NULL"), nullable=True, index=True
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("classes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    charge_model: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FeeChargeModel.YEARLY.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FeeStructureStatus.DRAFT.value, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    items = relationship(
        "FeeStructureItem", back_populates="structure", cascade="all, delete-orphan"
    )
    installments = relationship(
        "FeeInstallment", back_populates="structure", cascade="all, delete-orphan"
    )


class FeeStructureItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_structure_items"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_category_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    taxable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    refundable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    structure = relationship("FeeStructure", back_populates="items")


class FeeInstallment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_installments"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    structure = relationship("FeeStructure", back_populates="installments")


class StudentFeeAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "student_fee_accounts"
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "student_id",
            "academic_year_id",
            name="uq_student_fee_account_year",
        ),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("academic_years.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_structure_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_structures.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("enrollments.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=StudentFeeAccountStatus.ACTIVE.value, index=True
    )


class FeeAdjustment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_adjustments"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_fee_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_fee_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    fee_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_categories.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FeeAdjustmentType.DISCOUNT.value
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class FeeInvoice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_invoices"
    __table_args__ = (
        UniqueConstraint("school_id", "invoice_number", name="uq_fee_invoice_number"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_fee_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_fee_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    installment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_installments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    invoice_number: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    balance_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=FeeInvoiceStatus.DRAFT.value, index=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    items = relationship("FeeInvoiceItem", back_populates="invoice", cascade="all, delete-orphan")


class FeeInvoiceItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_invoice_items"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_categories.id", ondelete="SET NULL"), nullable=True
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("1"))
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    invoice = relationship("FeeInvoice", back_populates="items")


class Payment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_payments"
    __table_args__ = (UniqueConstraint("school_id", "payment_number", name="uq_fee_payment_number"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_fee_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("student_fee_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_invoices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    payment_number: Mapped[str] = mapped_column(String(64), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    method: Mapped[str] = mapped_column(String(32), nullable=False, default=PaymentMethod.ONLINE.value)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PaymentStatus.PENDING.value, index=True
    )
    gateway: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gateway_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gateway_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    allocations = relationship(
        "PaymentAllocation", back_populates="payment", cascade="all, delete-orphan"
    )


class PaymentAllocation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_payment_allocations"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    payment = relationship("Payment", back_populates="allocations")


class PaymentRefund(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_payment_refunds"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=RefundStatus.PENDING.value
    )
    gateway_refund_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class FeeReceipt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_receipts"
    __table_args__ = (UniqueConstraint("school_id", "receipt_number", name="uq_fee_receipt_number"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_payments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    receipt_number: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    pdf_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reprint_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fy_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)


class LateFeeRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "late_fee_rules"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fee_category_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("fee_categories.id", ondelete="SET NULL"), nullable=True
    )
    grace_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calculation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="FIXED")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    maximum_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    slabs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FeeSettings(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_settings"
    __table_args__ = (UniqueConstraint("school_id", name="uq_fee_settings_school"),)

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    allow_student_view_fees: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_partial_payments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    gateway_provider: Mapped[str] = mapped_column(String(64), nullable=False, default="mock")
    gateway_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="TEST")
    gateway_public_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="INV")
    receipt_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="REC")
    payment_prefix: Mapped[str] = mapped_column(String(16), nullable=False, default="PAY")
    linked_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    route_product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stakeholder_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    route_status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_started")
    platform_fee_percent: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), nullable=False, default=Decimal("0")
    )
    platform_fee_fixed: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    settlement_on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settlement_hold_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allow_platform_settlement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    bank_account_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    bank_account_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    bank_ifsc: Mapped[str | None] = mapped_column(String(16), nullable=True)
    beneficiary_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    legal_business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class FeeSequence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_sequences"
    __table_args__ = (
        UniqueConstraint("school_id", "seq_type", "fy_label", name="uq_fee_sequence_fy"),
    )

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq_type: Mapped[str] = mapped_column(String(32), nullable=False)  # INVOICE|PAYMENT|RECEIPT
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    fy_label: Mapped[str] = mapped_column(String(16), nullable=False)
    last_value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FeeAuditLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "fee_audit_logs"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    old_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_values: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SchoolFeeProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Where a school receives fees outside a gateway (UPI) and the tax identity printed on receipts."""

    __tablename__ = "school_fee_profiles"

    school_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("schools.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    upi_vpa: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payee_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), nullable=True)
    state_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    sac_code: Mapped[str] = mapped_column(String(8), nullable=False, default="999293")
    gst_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="EXEMPT")  # EXEMPT | TAXABLE
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=Decimal("0"))
    receipt_footer: Mapped[str | None] = mapped_column(String(300), nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
