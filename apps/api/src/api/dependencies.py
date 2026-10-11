from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.core.rls import apply_rls
from src.core.security import decode_token
from src.db.models import PlatformStaff, PlatformStaffPortal, SchoolMembership, User
from src.shared.enums import MembershipStatus, UserRole

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
    portals: tuple[str, ...] = ()


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    from src.core.config import get_settings

    cookie_token = request.cookies.get(get_settings().access_cookie_name)
    token = creds.credentials if creds is not None and creds.credentials else cookie_token
    if not token:
        raise UnauthorizedError("Missing bearer token")
    try:
        payload = decode_token(token)
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
    portals: tuple[str, ...] = ()

    if role == UserRole.PLATFORM_ADMIN and not user.is_platform_admin:
        raise UnauthorizedError("Not a platform admin")

    if membership_id is not None and role != UserRole.PLATFORM_ADMIN:
        membership = (
            await db.execute(select(SchoolMembership).where(SchoolMembership.id == membership_id))
        ).scalar_one_or_none()
        if (
            membership is None
            or membership.user_id != user.id
            or membership.status != MembershipStatus.ACTIVE.value
        ):
            raise UnauthorizedError("Membership is no longer active")
        role = UserRole(membership.role)
        school_id = membership.school_id
    elif role == UserRole.PLATFORM_STAFF:
        staff = (
            await db.execute(
                select(PlatformStaff).where(PlatformStaff.user_id == user.id, PlatformStaff.status == "ACTIVE")
            )
        ).scalar_one_or_none()
        if staff is None:
            raise UnauthorizedError("Staff access revoked")
        portal_rows = (
            await db.execute(select(PlatformStaffPortal.portal).where(PlatformStaffPortal.staff_id == staff.id))
        ).scalars().all()
        portals = tuple(portal_rows)
        school_id = None

    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=role,
        school_id=school_id,
        membership_id=membership_id,
        is_platform_admin=user.is_platform_admin,
        portals=portals,
    )


def require_roles(*roles: UserRole):
    async def _dep(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if user.role not in roles and not (UserRole.PLATFORM_ADMIN in roles and user.is_platform_admin):
            raise ForbiddenError(f"Requires one of: {', '.join(r.value for r in roles)}")
        return user

    return _dep


async def get_scoped_user(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_school_id: Annotated[UUID | None, Header(alias="X-School-Id")] = None,
) -> CurrentUser:
    """Finalize the tenant from the token, then enable Postgres RLS for this transaction."""
    if user.role in {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_STAFF}:
        if x_school_id:
            user.school_id = x_school_id
    else:
        if user.school_id is None:
            raise ForbiddenError("School context required")
        if x_school_id and x_school_id != user.school_id:
            raise ForbiddenError("Cross-tenant access denied")
    await apply_rls(
        db,
        user_id=user.id,
        school_id=user.school_id,
        role=user.role.value,
        membership_id=user.membership_id,
        portals=list(user.portals),
    )
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
