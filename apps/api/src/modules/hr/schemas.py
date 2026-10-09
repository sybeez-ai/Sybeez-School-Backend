from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StaffLeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str = Field(min_length=3, max_length=4000)


class StaffLeaveDecisionIn(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    decision_note: str | None = None


class StaffLeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    user_id: UUID
    start_date: date
    end_date: date
    reason: str
    status: str
    decided_by: UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


class PayrollRunCreate(BaseModel):
    label: str = Field(min_length=2, max_length=120)
    period_start: date
    period_end: date


class PayrollRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    label: str
    period_start: date
    period_end: date
    status: str
    created_by: UUID | None
    finalized_by: UUID | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PayrollPayslipCreate(BaseModel):
    teacher_id: UUID
    gross_amount: Decimal = Field(ge=0)
    deductions_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    notes: str | None = None


class PayrollPayslipUpdate(BaseModel):
    gross_amount: Decimal | None = Field(default=None, ge=0)
    deductions_amount: Decimal | None = Field(default=None, ge=0)
    net_amount: Decimal | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, max_length=32)
    paid_on: date | None = None
    notes: str | None = None


class PayrollPayslipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    payroll_run_id: UUID
    teacher_id: UUID
    gross_amount: Decimal
    deductions_amount: Decimal
    net_amount: Decimal
    status: str
    paid_on: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PayrollRunDetailOut(BaseModel):
    run: PayrollRunOut
    payslips: list[PayrollPayslipOut]

