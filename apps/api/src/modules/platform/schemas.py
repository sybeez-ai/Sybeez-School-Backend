from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class SchoolAdminCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, description="Required so school admin can login")


class SchoolCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9-]+$")
    country: str | None = None
    city: str | None = None
    address: str | None = None
    timezone: str = "UTC"
    admin: SchoolAdminCreate


class SchoolUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    country: str | None = None
    city: str | None = None
    address: str | None = None
    timezone: str | None = None


class SchoolOut(BaseModel):
    id: UUID
    name: str
    slug: str
    code: str | None
    status: str
    country: str | None
    city: str | None
    timezone: str
    created_at: datetime
    admin_email: str | None = None
    admin_user_id: UUID | None = None

    model_config = {"from_attributes": True}


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = "SCHOOL_ADMIN"
    message: str | None = None
    expires_in_days: int = Field(7, ge=1, le=30)


class InvitationOut(BaseModel):
    id: UUID
    school_id: UUID
    email: EmailStr
    role: str
    status: str
    expires_at: datetime
    invite_token: str | None = None

    model_config = {"from_attributes": True}


class PlatformDashboard(BaseModel):
    schools_count: int
    active_schools: int
    pending_invitations: int
