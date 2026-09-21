from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class ParentCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str | None = Field(default=None, min_length=8)
    phone: str | None = None
    student_id: UUID | None = None
    relationship: str = "guardian"
    is_primary: bool = False


class ParentOut(BaseModel):
    id: UUID
    school_id: UUID
    user_id: UUID
    full_name: str | None = None
    email: str | None = None
    phone: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class LinkParentRequest(BaseModel):
    parent_id: UUID
    relationship: str = "guardian"
    is_primary: bool = False
