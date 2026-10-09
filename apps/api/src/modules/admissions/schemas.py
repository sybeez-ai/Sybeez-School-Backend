from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import ConfigDict
from pydantic import BaseModel, EmailStr, Field

from src.modules.students.schemas import ParentLinkInput


class AdmissionEnquiryCreate(BaseModel):
    student_name: str = Field(min_length=2, max_length=200)
    student_dob: date | None = None
    desired_grade_id: UUID | None = None
    parent_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    notes: str | None = None


class AdmissionEnquiryUpdate(BaseModel):
    student_name: str | None = Field(default=None, min_length=2, max_length=200)
    student_dob: date | None = None
    desired_grade_id: UUID | None = None
    parent_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=32)
    email: EmailStr | None = None
    notes: str | None = None
    status: str | None = Field(default=None, max_length=32)


class AdmissionEnquiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID
    created_by: UUID | None
    student_name: str
    student_dob: date | None
    desired_grade_id: UUID | None
    parent_name: str | None
    phone: str | None
    email: EmailStr | None
    notes: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class AdmissionApplicationCreate(BaseModel):
    enquiry_id: UUID | None = None
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    desired_grade_id: UUID | None = None
    notes: str | None = None


class AdmissionApplicationUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    desired_grade_id: UUID | None = None
    notes: str | None = None


class AdmissionApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    school_id: UUID
    enquiry_id: UUID | None
    created_by: UUID | None
    first_name: str
    last_name: str
    email: EmailStr
    phone: str | None
    date_of_birth: date | None
    desired_grade_id: UUID | None
    status: str
    submitted_at: datetime
    notes: str | None
    created_at: datetime
    updated_at: datetime


class AdmissionStatusChangeIn(BaseModel):
    status: str = Field(min_length=2, max_length=32)
    note: str | None = None


class AdmissionStatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    application_id: UUID
    changed_by: UUID | None
    from_status: str | None
    to_status: str
    note: str | None
    created_at: datetime


class ConvertEnquiryIn(BaseModel):
    """Optional overrides while converting an enquiry to an application."""

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    desired_grade_id: UUID | None = None
    notes: str | None = None


class AdmitApplicationIn(BaseModel):
    class_id: UUID
    academic_year_id: UUID
    admission_no: str | None = Field(default=None, max_length=64)
    admission_date: date | None = None
    roll_number: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=8)
    # Optional overrides for student identity
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=32)
    date_of_birth: date | None = None
    # Optional parent links at admit-time
    parents: list[ParentLinkInput] = []


class AdmitApplicationOut(BaseModel):
    application: AdmissionApplicationOut
    student_id: UUID
    enrollment_id: UUID | None = None

