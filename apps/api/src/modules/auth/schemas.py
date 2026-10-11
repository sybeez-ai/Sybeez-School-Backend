from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    school_id: UUID | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str
    school_id: UUID | None = None
    user_id: UUID
    full_name: str
    access_expires_in: int = 0
    refresh_expires_in: int = 0


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=200)


class MeResponse(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str
    role: str
    school_id: UUID | None
    is_platform_admin: bool
