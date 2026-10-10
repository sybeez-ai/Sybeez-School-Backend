"""Request and response shapes for /api/v1/finance."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=32)
    address: str | None = None
    active: bool = True


class BranchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = None
    active: bool | None = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    name: str
    code: str
    address: str | None = None
    active: bool


class FinancialYearIn(BaseModel):
    label: str = Field(min_length=4, max_length=16, examples=["2026-27"])
    start_date: date
    end_date: date
    is_current: bool = False


class FinancialYearOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    label: str
    start_date: date
    end_date: date
    status: str
    is_current: bool


class StaffScopeIn(BaseModel):
    user_id: UUID
    branch_id: UUID


class StaffScopeOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    branch_id: UUID | None = None


class AccessOut(BaseModel):
    role: str
    school_id: UUID
    permissions: list[str]
    branch_ids: list[UUID] | None = None


class StudentSearchHit(BaseModel):
    student_id: UUID
    full_name: str
    admission_no: str | None = None
    phone: str | None = None
    category: str
    branch_id: UUID | None = None
    outstanding: Decimal


class FeeProfileIn(BaseModel):
    category: str = "GENERAL"
    branch_id: UUID | None = None
    sibling_group_id: UUID | None = None
    transport_opt_in: bool = False
    hostel_opt_in: bool = False


class FeeProfileOut(BaseModel):
    student_id: UUID
    school_id: UUID
    category: str
    branch_id: UUID | None = None
    sibling_group_id: UUID | None = None
    transport_opt_in: bool
    hostel_opt_in: bool


class SiblingGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class SiblingGroupOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str


class ConcessionRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=64)
    rule_type: str
    value_type: str
    value: Decimal = Field(gt=0)
    fee_category_id: UUID | None = None
    student_category: str | None = None
    active: bool = True


class ConcessionRulePatch(BaseModel):
    name: str | None = None
    value: Decimal | None = Field(default=None, gt=0)
    active: bool | None = None
    fee_category_id: UUID | None = None
    student_category: str | None = None


class ConcessionRuleOut(BaseModel):
    id: UUID
    school_id: UUID
    name: str
    code: str
    rule_type: str
    value_type: str
    value: Decimal
    fee_category_id: UUID | None = None
    student_category: str | None = None
    active: bool


class ConcessionRequestIn(BaseModel):
    student_id: UUID
    rule_id: UUID | None = None
    fee_category_id: UUID | None = None
    value_type: str = "FLAT"
    value: Decimal = Field(gt=0)
    reason: str = Field(min_length=1)


class ConcessionOut(BaseModel):
    id: UUID
    school_id: UUID
    student_id: UUID
    rule_id: UUID | None = None
    fee_category_id: UUID | None = None
    value_type: str
    value: Decimal
    computed_amount: Decimal
    reason: str
    status: str
    requested_by: UUID | None = None
    approved_by: UUID | None = None


class DecisionIn(BaseModel):
    note: str | None = None


class DueLine(BaseModel):
    invoice_id: UUID
    invoice_number: str
    due_date: date
    total_amount: Decimal
    balance_amount: Decimal
    status: str
    description: str | None = None


class TenderIn(BaseModel):
    method: str
    amount: Decimal = Field(gt=0)
    instrument_no: str | None = None
    bank_name: str | None = None
    instrument_date: date | None = None
    reference: str | None = None


class AllocationIn(BaseModel):
    invoice_id: UUID
    amount: Decimal = Field(gt=0)


class CollectIn(BaseModel):
    student_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=255)
    branch_id: UUID | None = None
    allocations: list[AllocationIn] = Field(min_length=1)
    tenders: list[TenderIn] = Field(min_length=1)
    notes: str | None = None


class TenderOut(BaseModel):
    id: UUID
    method: str
    amount: Decimal
    status: str
    instrument_no: str | None = None
    bank_name: str | None = None
    instrument_date: date | None = None
    reference: str | None = None


class AllocationOut(BaseModel):
    invoice_id: UUID
    amount: Decimal


class CollectionOut(BaseModel):
    payment_id: UUID
    receipt_id: UUID
    receipt_number: str
    receipt_status: str
    amount: Decimal
    method: str
    student_id: UUID
    branch_id: UUID | None = None
    tenders: list[TenderOut]
    allocations: list[AllocationOut]


class CancelReceiptIn(BaseModel):
    reason: str = Field(min_length=3)


class BounceIn(BaseModel):
    reason: str = Field(min_length=3)
    charge: Decimal = Field(default=Decimal("0"), ge=0)


class ChequeOut(BaseModel):
    id: UUID
    school_id: UUID
    payment_id: UUID
    receipt_id: UUID | None = None
    receipt_number: str | None = None
    branch_id: UUID | None = None
    method: str
    amount: Decimal
    status: str
    instrument_no: str | None = None
    bank_name: str | None = None
    instrument_date: date | None = None
    student_id: UUID | None = None


class LedgerRow(BaseModel):
    id: UUID
    entry_date: date
    entry_type: str
    debit: Decimal
    credit: Decimal
    balance: Decimal
    narration: str
    ref_type: str
    ref_id: UUID | None = None


class DayCloseIn(BaseModel):
    business_date: date
    branch_id: UUID | None = None
    opening_cash: Decimal = Field(default=Decimal("0"), ge=0)
    physical_cash: Decimal = Field(ge=0)
    notes: str | None = None


class DayCloseOut(BaseModel):
    id: UUID
    school_id: UUID
    branch_id: UUID | None = None
    cashier_user_id: UUID
    business_date: date
    opening_cash: Decimal
    physical_cash: Decimal
    system_cash: Decimal
    system_cheque: Decimal
    system_upi: Decimal
    system_card: Decimal
    system_neft: Decimal
    system_online: Decimal
    system_total: Decimal
    variance: Decimal
    notes: str | None = None
    status: str
    verified_by: UUID | None = None


class DashboardOut(BaseModel):
    currency: str
    today_collection: Decimal
    today_by_mode: dict[str, Decimal]
    outstanding: Decimal
    overdue: Decimal
    pending_approvals: int
    trend: list[dict]


class FineWaiverIn(BaseModel):
    invoice_id: UUID
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3)


class ApprovalOut(BaseModel):
    id: UUID
    school_id: UUID
    entity_type: str
    entity_id: UUID
    status: str
    amount: Decimal
    reason: str
    requested_by: UUID | None = None
    approved_by: UUID | None = None
    decision_note: str | None = None


class ReminderQueuedOut(BaseModel):
    queued: bool
    school_id: UUID
    job: str


class ReminderLogOut(BaseModel):
    id: UUID
    school_id: UUID
    invoice_id: UUID
    kind: str
    sent_on: date
    created_at: datetime


class AuditRow(BaseModel):
    id: UUID
    action: str
    entity_type: str
    entity_id: UUID | None = None
    actor_user_id: UUID | None = None
    reason: str | None = None
    old_values: str | None = None
    new_values: str | None = None
    ip_address: str | None = None
    created_at: datetime
