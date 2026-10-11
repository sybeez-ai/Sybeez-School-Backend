from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.shared.enums import UserRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    school_id: UUID | None = None
    membership_id: UUID | None = None
    # portal login page: platform | school | hrm | payroll | finance | teacher | student | parent | staff
    # hrm also accepts HR, HR_MANAGER, and PAYROLL_APPROVER. staff is UserRole.STAFF self-service.
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
    role: UserRole | None = Field(
        default=None,
        description="Role issued in this access token. Includes ACCOUNTANT, CASHIER, and AUDITOR.",
    )
    school_id: UUID | None = None
    user_id: UUID | None = None
    full_name: str | None = None
    platform_role: str | None = None
    access_expires_in: int | None = None
    refresh_expires_in: int | None = None
    # Present when the account has several memberships and login did not pick one.
    selection_required: bool = False
    memberships: list["MembershipOut"] = Field(default_factory=list)
    platform_login_available: bool = False
    # Present when Cognito requires first-login password change
    challenge: str | None = None
    session: str | None = None
    email: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


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
    role: UserRole
    school_id: UUID | None
    is_platform_admin: bool
    platform_role: str | None = None
    is_active: bool = True
    portals: list[str] = []


class MembershipOut(BaseModel):
    id: UUID
    school_id: UUID
    school_name: str
    role: UserRole
    status: str


class SwitchSchoolRequest(BaseModel):
    school_id: UUID
    role: UserRole | None = None


class MessageOut(BaseModel):
    message: str = "ok"
