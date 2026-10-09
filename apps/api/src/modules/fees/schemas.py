from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeeCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=1, max_length=64)
    description: str | None = None
    active: bool = True


class FeeCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    active: bool | None = None


class FeeCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    description: str | None = None
    active: bool


class StructureItemIn(BaseModel):
    fee_category_id: UUID
    amount: Decimal = Field(gt=0)
    taxable: bool = False
    refundable: bool = False
    mandatory: bool = True
    description: str | None = None


class InstallmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    due_date: date
    sequence: int = Field(default=1, ge=1)
    amount: Decimal = Field(gt=0)


class FeeStructureCreate(BaseModel):
    academic_year_id: UUID
    name: str = Field(min_length=1, max_length=255)
    grade_id: UUID | None = None
    section_id: UUID | None = None
    charge_model: str = "YEARLY"
    currency: str = "INR"
    effective_from: date | None = None
    effective_to: date | None = None
    items: list[StructureItemIn] = Field(default_factory=list)
    installments: list[InstallmentIn] = Field(default_factory=list)


class FeeStructureUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    grade_id: UUID | None = None
    section_id: UUID | None = None
    charge_model: str | None = None
    active: bool | None = None
    effective_from: date | None = None
    effective_to: date | None = None


class StructureItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fee_category_id: UUID
    amount: Decimal
    taxable: bool
    refundable: bool
    mandatory: bool
    description: str | None = None
    category_name: str | None = None
    category_code: str | None = None


class InstallmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    due_date: date
    sequence: int
    amount: Decimal


class FeeStructureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    academic_year_id: UUID
    name: str
    grade_id: UUID | None = None
    section_id: UUID | None = None
    charge_model: str
    status: str
    active: bool
    currency: str
    effective_from: date | None = None
    effective_to: date | None = None
    total_amount: Decimal = Decimal("0")
    items: list[StructureItemOut] = []
    installments: list[InstallmentOut] = []


class AdjustmentCreate(BaseModel):
    fee_category_id: UUID | None = None
    type: str = "DISCOUNT"
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1)


class AdjustmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    student_fee_account_id: UUID
    fee_category_id: UUID | None = None
    type: str
    amount: Decimal
    reason: str
    created_by: UUID | None = None
    approved_by: UUID | None = None
    created_at: datetime


class InvoiceGenerateIn(BaseModel):
    academic_year_id: UUID
    fee_structure_id: UUID
    installment_id: UUID | None = None
    grade_id: UUID | None = None
    section_id: UUID | None = None
    student_ids: list[UUID] | None = None
    due_date: date | None = None
    invoice_date: date | None = None


class InvoiceItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fee_category_id: UUID | None = None
    description: str
    quantity: Decimal
    unit_amount: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal


class InvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_fee_account_id: UUID
    student_id: UUID | None = None
    student_name: str | None = None
    class_name: str | None = None
    installment_id: UUID | None = None
    invoice_number: str
    invoice_date: date
    due_date: date
    currency: str
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    paid_amount: Decimal
    balance_amount: Decimal
    status: str
    items: list[InvoiceItemOut] = []


class GenerateResultOut(BaseModel):
    created: int
    skipped: int
    total_amount: Decimal
    invoices: list[InvoiceOut]


class ManualPaymentIn(BaseModel):
    invoice_id: UUID
    amount: Decimal = Field(gt=0)
    method: str = "CASH"
    reference: str | None = None
    paid_at: datetime | None = None
    notes: str | None = None


class PaymentOrderIn(BaseModel):
    invoice_id: UUID
    amount: Decimal | None = None


class PaymentOrderOut(BaseModel):
    payment_id: UUID
    payment_number: str
    order_id: str
    amount: Decimal
    currency: str
    gateway: str
    public_key: str | None = None


class PaymentVerifyIn(BaseModel):
    payment_id: UUID
    gateway_order_id: str
    gateway_payment_id: str | None = None
    signature: str | None = None


class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    student_fee_account_id: UUID
    invoice_id: UUID | None = None
    payment_number: str
    amount: Decimal
    currency: str
    method: str
    status: str
    gateway: str | None = None
    gateway_order_id: str | None = None
    gateway_payment_id: str | None = None
    gateway_reference: str | None = None
    paid_at: datetime | None = None
    notes: str | None = None
    receipt_id: UUID | None = None
    receipt_number: str | None = None


class RefundIn(BaseModel):
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=1)


class ReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    payment_id: UUID
    receipt_number: str
    issued_at: datetime
    amount: Decimal | None = None
    currency: str | None = None
    student_name: str | None = None
    invoice_number: str | None = None
    method: str | None = None


class StudentFeeSummaryOut(BaseModel):
    student_id: UUID
    student_name: str
    class_name: str | None = None
    academic_year_id: UUID | None = None
    account_id: UUID | None = None
    assessed: Decimal
    paid: Decimal
    outstanding: Decimal
    overdue: Decimal
    next_due_date: date | None = None
    next_due_amount: Decimal | None = None
    status: str


class LedgerEntryOut(BaseModel):
    date: date
    description: str
    debit: Decimal
    credit: Decimal
    balance: Decimal


class FeesDashboardOut(BaseModel):
    currency: str
    assessed: Decimal
    collected: Decimal
    outstanding: Decimal
    overdue: Decimal
    today_collection: Decimal
    recent_payments: list[PaymentOut] = []


class FeeSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    allow_student_view_fees: bool
    allow_partial_payments: bool
    gateway_provider: str
    gateway_mode: str
    gateway_public_key: str | None = None
    invoice_prefix: str
    receipt_prefix: str
    payment_prefix: str


class FeeSettingsUpdate(BaseModel):
    currency: str | None = None
    allow_student_view_fees: bool | None = None
    allow_partial_payments: bool | None = None
    gateway_provider: str | None = None
    gateway_mode: str | None = None
    gateway_public_key: str | None = None


class LateFeeRuleIn(BaseModel):
    fee_category_id: UUID | None = None
    grace_days: int = 0
    calculation_type: str = "FIXED"
    amount: Decimal = Field(ge=0)
    maximum_amount: Decimal | None = None
    active: bool = True


class LateFeeRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    fee_category_id: UUID | None = None
    grace_days: int
    calculation_type: str
    amount: Decimal
    maximum_amount: Decimal | None = None
    active: bool


class CollectionRowOut(BaseModel):
    date: date
    method: str
    total: Decimal


class OutstandingRowOut(BaseModel):
    student_id: UUID
    student_name: str
    class_name: str | None = None
    invoice_number: str
    due_date: date
    total_amount: Decimal
    paid_amount: Decimal
    balance_amount: Decimal
    status: str
    days_overdue: int = 0
