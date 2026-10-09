from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    school_id: UUID | None = None
    # portal login page: platform | school | hrm | teacher | student | parent
    portal: str | None = None


class CompleteNewPasswordRequest(BaseModel):
    email: EmailStr
    session: str = Field(min_length=10)
    new_password: str = Field(min_length=8)
    portal: str | None = None


class TokenResponse(BaseModel):
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str = "bearer"
    role: str | None = None
    school_id: UUID | None = None
    user_id: UUID | None = None
    full_name: str | None = None
    platform_role: str | None = None
    # Present when Cognito requires first-login password change
    challenge: str | None = None
    session: str | None = None
    email: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ConfirmForgotPasswordRequest(BaseModel):
    email: EmailStr
    confirmation_code: str = Field(min_length=4, max_length=32)
    new_password: str = Field(min_length=8)


class UpdateProfileRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)


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
    platform_role: str | None = None
    is_active: bool = True


class MessageOut(BaseModel):
    message: str = "ok"
