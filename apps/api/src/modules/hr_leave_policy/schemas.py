from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _strip(v):
    return v.strip() if isinstance(v, str) else v


class LeaveTypeIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    annual_quota: Decimal | None = Field(default=None, ge=0, le=366, decimal_places=1)
    accrual: Literal["ANNUAL", "MONTHLY"] = "ANNUAL"
    is_paid: bool = True
    max_consecutive_days: int | None = Field(default=None, ge=1, le=366)
    sort_order: int = Field(default=0, ge=0, le=1000)

    _trim = field_validator("name", "description", mode="before")(_strip)

    @field_validator("code", mode="before")
    @classmethod
    def _code(cls, v):
        v = str(v or "").strip().upper().replace(" ", "_")
        if not v or not all(c.isascii() and (c.isalnum() or c == "_") for c in v):
            raise ValueError("Use letters, digits or underscores for the code")
        return v


class LeaveTypeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    # Nullable: null means no balance limit.
    annual_quota: Decimal | None = Field(default=None, ge=0, le=366, decimal_places=1)
    accrual: Literal["ANNUAL", "MONTHLY"] | None = None
    is_paid: bool | None = None
    max_consecutive_days: int | None = Field(default=None, ge=1, le=366)
    is_active: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)

    _trim = field_validator("name", "description", mode="before")(_strip)


class LeaveTypeOut(BaseModel):
    id: UUID
    code: str
    name: str
    description: str | None = None
    annual_quota: Decimal | None = None
    accrual: str
    is_paid: bool
    max_consecutive_days: int | None = None
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class HolidayIn(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=120)

    _trim = field_validator("name", mode="before")(_strip)


class HolidayOut(BaseModel):
    id: UUID
    holiday_date: date
    name: str
    created_at: datetime


class LeaveBalanceOut(BaseModel):
    leave_type_id: UUID
    code: str
    name: str
    is_paid: bool
    accrual: str
    annual_quota: Decimal | None = None
    entitled: Decimal | None = None
    used: Decimal
    pending: Decimal
    # None when the type has no balance limit.
    available: Decimal | None = None


class EmployeeBalances(BaseModel):
    year: int
    user_id: UUID
    employee_name: str
    balances: list[LeaveBalanceOut]


class CalendarEntry(BaseModel):
    leave_id: UUID
    user_id: UUID
    staff_name: str | None = None
    employee_code: str | None = None
    leave_type_name: str | None = None
    status: str
    start_date: date
    end_date: date
    working_days: Decimal | None = None


class LeaveCalendar(BaseModel):
    date_from: date
    date_to: date
    working_weekdays: list[int]
    holidays: list[HolidayOut]
    entries: list[CalendarEntry]


class WorkingDays(BaseModel):
    start_date: date
    end_date: date
    working_days: int
    non_working: list[date]
