from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.shared.enums import UserRole


class InvitationIn(BaseModel):
    email: EmailStr
    role: UserRole
    full_name: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=2000)
    expires_in_days: int = Field(default=7, ge=1, le=30)


class InvitationOut(BaseModel):
    id: UUID
    school_id: UUID
    email: str
    role: str
    status: str
    expires_at: datetime
    invite_token: str | None = None

    model_config = {"from_attributes": True}


class RoleGrantIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: UserRole


class RoleChangeIn(BaseModel):
    role: UserRole


# Per-person portal grants. Each portal maps to the school role its login accepts.
PORTAL_GRANT_ROLES: dict[str, str] = {
    "finance": "ACCOUNTANT",
    "payroll": "PAYROLL_ADMIN",
    "hrm": "HR_MANAGER",
    "teacher": "TEACHER",
}


class PortalGrantIn(BaseModel):
    portal: str = Field(pattern="^(finance|payroll|hrm|teacher)$")


class PermissionChangeIn(BaseModel):
    permission: str = Field(min_length=3, max_length=64)
    effect: str = Field(pattern="^(GRANT|REVOKE)$")


class MembershipOut(BaseModel):
    id: UUID
    user_id: UUID
    school_id: UUID
    role: str
    status: str
    email: str | None = None
    full_name: str | None = None


class EffectivePermissionsOut(BaseModel):
    user_id: UUID
    membership_id: UUID | None
    role: str
    school_id: UUID | None
    permissions: list[str]


class MatrixOut(BaseModel):
    permissions: dict[str, str]
    roles: dict[str, list[str]]
