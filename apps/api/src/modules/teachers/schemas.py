from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class TeacherCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    employee_code: str | None = None
    title: str | None = None


class TeacherOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    full_name: str | None = None
    email: str | None = None
    employee_code: str | None
    title: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
