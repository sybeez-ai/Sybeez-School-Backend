from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.core.security import decode_token
from src.db.models import SchoolMembership, User
from src.shared.enums import MembershipStatus, PlatformRole, UserRole

bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: UUID
    email: str
    full_name: str
    role: UserRole
    school_id: UUID | None
    membership_id: UUID | None
    is_platform_admin: bool
    platform_role: str | None = None


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise UnauthorizedError("Missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc
    if payload.get("type") != "access":
        raise UnauthorizedError("Invalid token type")

    user_id = UUID(payload["sub"])
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User not found")

    role = UserRole(payload.get("role", UserRole.STUDENT.value))
    school_id = UUID(payload["school_id"]) if payload.get("school_id") else None
    membership_id = UUID(payload["membership_id"]) if payload.get("membership_id") else None

    if role == UserRole.PLATFORM_ADMIN and not user.is_platform_admin:
        raise UnauthorizedError("Not a platform admin")

    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=role,
        school_id=school_id,
        membership_id=membership_id,
        is_platform_admin=user.is_platform_admin,
        platform_role=user.platform_role or payload.get("platform_role"),
    )


def require_roles(*roles: UserRole):
    async def _dep(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles and not (UserRole.PLATFORM_ADMIN in roles and user.is_platform_admin):
            needed = ", ".join(r.value for r in roles)
            if UserRole.PLATFORM_ADMIN in roles:
                msg = (
                    f"Requires one of: {needed}. "
                    f"Current session role is {user.role.value}. "
                    "Sign in at /login/platform with a platform admin account."
                )
            else:
                msg = f"Requires one of: {needed}"
            raise ForbiddenError(msg)
        return user

    return _dep


def require_super_admin(
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
) -> CurrentUser:
    if user.platform_role != PlatformRole.SUPER_ADMIN.value:
        raise ForbiddenError("Super admin only")
    return user


def require_admin_or_above(
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
) -> CurrentUser:
    if user.platform_role not in {
        PlatformRole.SUPER_ADMIN.value,
        PlatformRole.ADMIN.value,
        PlatformRole.PLATFORM_ADMIN.value,
        PlatformRole.OPERATIONS.value,
    }:
        raise ForbiddenError("Admin only")
    return user


async def require_school_scope(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    x_school_id: Annotated[UUID | None, Header(alias="X-School-Id")] = None,
) -> CurrentUser:
    """Ensure school-scoped requests carry a valid tenant school_id."""
    if user.role == UserRole.PLATFORM_ADMIN:
        if x_school_id:
            user.school_id = x_school_id
        return user
    if user.school_id is None:
        raise ForbiddenError("School context required")
    if x_school_id and x_school_id != user.school_id:
        raise ForbiddenError("Cross-tenant access denied")
    return user


async def assert_membership(
    db: AsyncSession, user_id: UUID, school_id: UUID, roles: set[UserRole] | None = None
) -> SchoolMembership:
    q = select(SchoolMembership).where(
        SchoolMembership.user_id == user_id,
        SchoolMembership.school_id == school_id,
        SchoolMembership.status == MembershipStatus.ACTIVE.value,
    )
    result = await db.execute(q)
    membership = result.scalar_one_or_none()
    if membership is None:
        raise ForbiddenError("No active membership for this school")
    if roles and UserRole(membership.role) not in roles:
        raise ForbiddenError("Insufficient school role")
    return membership
