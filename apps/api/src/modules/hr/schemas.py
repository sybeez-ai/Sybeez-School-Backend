from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.db.models.hr import PAYSLIP_STATUSES


class StaffLeaveRequestCreate(BaseModel):
    start_date: date
    end_date: date
    reason: str = Field(min_length=3, max_length=4000)
    # Required once the school has configured leave types.
    leave_type_id: UUID | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, v):
        return v.strip() if isinstance(v, str) else v


class StaffLeaveRevokeIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def _trim_reason(cls, v):
        return v.strip() if isinstance(v, str) else v


class StaffLeaveOnBehalfIn(StaffLeaveRequestCreate):
    """HR enters leave for an employee (phone call, paper form). Notice rules do not apply."""

    user_id: UUID
    approve: bool = False
    decision_note: str | None = Field(default=None, max_length=2000)


class StaffLeaveDecisionIn(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    decision_note: str | None = Field(default=None, max_length=2000)

    @field_validator("decision_note", mode="before")
    @classmethod
    def _trim_note(cls, v):
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class StaffLeaveRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    user_id: UUID
    staff_name: str | None = None
    employee_code: str | None = None
    department_name: str | None = None
    start_date: date
    end_date: date
    days: int | None = None
    working_days: Decimal | None = None
    leave_type_id: UUID | None = None
    leave_type_name: str | None = None
    leave_type_is_paid: bool | None = None
    resubmitted_from_id: UUID | None = None
    resubmitted_as_id: UUID | None = None
    reason: str
    status: str
    decided_by: UUID | None
    decided_by_name: str | None = None
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
    payslip_count: int | None = None
    paid_count: int | None = None


class PayrollPayslipCreate(BaseModel):
    teacher_id: UUID
    # Payslip amounts are stored as Numeric(12, 2).
    gross_amount: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    deductions_amount: Decimal = Field(default=Decimal("0.00"), ge=0, max_digits=12, decimal_places=2)
    notes: str | None = None


class PayrollPayslipUpdate(BaseModel):
    gross_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    deductions_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    net_amount: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    status: str | None = Field(default=None, max_length=32)
    paid_on: date | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip().upper()
        if s not in PAYSLIP_STATUSES:
            raise ValueError(f"status must be one of {', '.join(PAYSLIP_STATUSES)}")
        return s


class PayrollPayslipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    school_id: UUID
    payroll_run_id: UUID
    teacher_id: UUID | None = None
    employee_id: UUID | None = None
    gross_amount: Decimal
    deductions_amount: Decimal
    net_amount: Decimal
    status: str
    paid_on: date | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    employee_name: str | None = None
    employee_code: str | None = None
    department_name: str | None = None
    run_label: str | None = None
    period_start: date | None = None
    period_end: date | None = None


class PayrollRunDetailOut(BaseModel):
    run: PayrollRunOut
    payslips: list[PayrollPayslipOut]

