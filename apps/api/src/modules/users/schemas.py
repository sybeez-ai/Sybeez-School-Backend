from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserStatusUpdate(BaseModel):
    is_active: bool


class SchoolUserOut(BaseModel):
    id: UUID
    email: str
    full_name: str
    is_active: bool
    role: str | None = None
    school_id: UUID | None = None


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
