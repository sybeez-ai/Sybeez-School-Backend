from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

StaffType = Literal["TEACHING", "NON_TEACHING"]
EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "TEMPORARY"]
EmployeeStatus = Literal["ACTIVE", "INACTIVE", "LEFT"]
ExitReason = Literal["RESIGNATION", "TERMINATION", "RETIREMENT", "CONTRACT_END", "OTHER"]
EmployeeSort = Literal["name", "code", "joining_date", "department", "designation", "status", "created_at"]


def _clean(v):
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class _HrFields(BaseModel):
    department_id: UUID | None = None
    designation_id: UUID | None = None
    reporting_manager_id: UUID | None = None
    employment_type: EmploymentType | None = None
    address: str | None = Field(default=None, max_length=2000)
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)


class EmployeeCreate(_HrFields):
    staff_type: StaffType
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = Field(default=None, max_length=255)
    grant_access: bool = False

    @model_validator(mode="after")
    def _teaching_needs_login_email(self) -> EmployeeCreate:
        if self.staff_type == "TEACHING" and not self.email:
            raise ValueError("email is required for teaching staff (it is their portal login)")
        if self.grant_access and not self.email:
            raise ValueError("email is required when granting portal access")
        return self


class EmployeeUpdate(_HrFields):
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = Field(default=None, max_length=255)
    status: EmployeeStatus | None = None
    # When a department/designation/manager/employment-type change takes effect (defaults to today).
    effective_date: date | None = None
    change_note: str | None = Field(default=None, max_length=500)

    _clean_note = field_validator("change_note", mode="before")(_clean)


class EmployeeExitIn(BaseModel):
    exit_date: date
    exit_reason: ExitReason
    notes: str | None = Field(default=None, max_length=2000)

    _clean_notes = field_validator("notes", mode="before")(_clean)


class EmployeeRehireIn(BaseModel):
    rejoin_date: date
    note: str | None = Field(default=None, max_length=500)

    _clean_note = field_validator("note", mode="before")(_clean)


class StaffLoginIn(BaseModel):
    email: EmailStr
    password: str | None = Field(default=None, min_length=8, max_length=128)


class EmployeeHistoryOut(BaseModel):
    id: UUID
    event_type: str
    effective_date: date
    from_value: str | None = None
    to_value: str | None = None
    note: str | None = None
    actor_name: str | None = None
    created_at: datetime


class EmployeeOut(BaseModel):
    id: UUID
    school_id: UUID
    staff_type: StaffType
    teacher_id: UUID | None
    user_id: UUID | None
    has_login: bool
    full_name: str
    email: str | None
    phone: str | None
    employee_code: str | None
    title: str | None
    date_of_birth: date | None
    joining_date: date | None
    qualification: str | None
    photo_url: str | None
    status: str
    is_active: bool
    department_id: UUID | None = None
    department_name: str | None = None
    designation_id: UUID | None = None
    designation_name: str | None = None
    reporting_manager_id: UUID | None = None
    reporting_manager_name: str | None = None
    employment_type: str | None
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    notes: str | None
    exit_date: date | None = None
    exit_reason: str | None = None
    exit_notes: str | None = None
    created_at: datetime
    updated_at: datetime
    temporary_password: str | None = None
    invite_token: str | None = None
