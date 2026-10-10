from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

StaffType = Literal["TEACHING", "NON_TEACHING"]
EmploymentType = Literal["FULL_TIME", "PART_TIME", "CONTRACT", "TEMPORARY"]
EmployeeStatus = Literal["ACTIVE", "INACTIVE", "LEFT"]


class _HrFields(BaseModel):
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type: EmploymentType | None = None
    address: str | None = Field(default=None, max_length=2000)
    emergency_contact_name: str | None = Field(default=None, max_length=200)
    emergency_contact_phone: str | None = Field(default=None, max_length=32)
    notes: str | None = Field(default=None, max_length=4000)


class EmployeeCreate(_HrFields):
    staff_type: StaffType
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=8)
    phone: str | None = Field(default=None, max_length=32)
    employee_code: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    joining_date: date | None = None
    qualification: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _teaching_needs_login_email(self) -> EmployeeCreate:
        if self.staff_type == "TEACHING" and not self.email:
            raise ValueError("email is required for teaching staff (it is their portal login)")
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
    employment_type: str | None
    address: str | None
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    temporary_password: str | None = None
