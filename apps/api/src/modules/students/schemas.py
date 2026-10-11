from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class StudentUpdate(BaseModel):
    admission_no: str | None = Field(default=None, max_length=64)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, max_length=32)


class StudentCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    admission_no: str | None = None
    date_of_birth: date | None = None
    class_id: UUID | None = None
    academic_year_id: UUID | None = None


class StudentOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    full_name: str | None = None
    email: str | None = None
    admission_no: str | None
    date_of_birth: date | None
    created_at: datetime

    model_config = {"from_attributes": True}
