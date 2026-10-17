from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.exceptions import ForbiddenError, UnauthorizedError
from src.core.security import decode_token
from src.db.models import RefreshToken, SchoolMembership, User
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
    portals: tuple[str, ...] = ()


_PASSWORD_CHANGE_ALLOWED = ("/auth/me", "/auth/change-password", "/auth/logout", "/auth/refresh")


async def get_current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    from src.core.config import get_settings

    settings = get_settings()
    bearer_token = creds.credentials if creds is not None and creds.credentials else None
    cookie_token = request.cookies.get(settings.access_cookie_name)
    token = bearer_token or cookie_token
    if not token:
        raise UnauthorizedError("Missing bearer token")
    request.state.auth_via_cookie = bearer_token is None
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
    # An admin-issued temporary password must be replaced before the account can do anything
    # else; only the auth endpoints needed to change it (and sign out) stay open.
    if getattr(user, "must_change_password", False) and not request.url.path.rstrip("/").endswith(
        _PASSWORD_CHANGE_ALLOWED
    ):
        raise ForbiddenError("PASSWORD_CHANGE_REQUIRED: change your temporary password to continue")

    # Access tokens carry the refresh session they were issued with; once that session is
    # revoked (logout, rotation, password reset) the access token stops working too.
    sid = payload.get("sid")
    if sid is not None:
        revoked = (
            await db.execute(
                select(RefreshToken.revoked).where(RefreshToken.jti == sid, RefreshToken.user_id == user_id)
            )
        ).scalar_one_or_none()
        if revoked is None or revoked:
            raise UnauthorizedError("Session has ended")

    role = UserRole(payload.get("role", UserRole.STUDENT.value))
    school_id = UUID(payload["school_id"]) if payload.get("school_id") else None
    membership_id = UUID(payload["membership_id"]) if payload.get("membership_id") else None
    portals: tuple[str, ...] = ()

    if role == UserRole.PLATFORM_ADMIN and not user.is_platform_admin:
        raise UnauthorizedError("Not a platform admin")

    # A suspended or removed membership loses access at once; a changed role needs a new token.
    if membership_id is not None:
        membership = (
            await db.execute(
                select(SchoolMembership.status, SchoolMembership.role, SchoolMembership.school_id).where(
                    SchoolMembership.id == membership_id, SchoolMembership.user_id == user_id
                )
            )
        ).one_or_none()
        if (
            membership is None
            or membership.status != MembershipStatus.ACTIVE.value
            or membership.school_id != school_id
        ):
            raise UnauthorizedError("School membership is not active")
        if membership.role != role.value:
            raise UnauthorizedError("Session has ended")

    if role == UserRole.PLATFORM_STAFF:
        from src.db.models import PlatformStaff, PlatformStaffPortal

        staff = (
            await db.execute(
                select(PlatformStaff).where(PlatformStaff.user_id == user.id, PlatformStaff.status == "ACTIVE")
            )
        ).scalar_one_or_none()
        if staff is not None:
            portals = tuple(
                (
                    await db.execute(
                        select(PlatformStaffPortal.portal).where(PlatformStaffPortal.staff_id == staff.id)
                    )
                ).scalars().all()
            )

    header_school = request.headers.get("x-school-id")
    if header_school:
        try:
            header_id = UUID(header_school)
        except ValueError as exc:
            raise ForbiddenError("Cross-tenant access denied") from exc
        if school_id is None and role in {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_STAFF}:
            school_id = header_id
        elif school_id is not None and header_id != school_id:
            raise ForbiddenError("Cross-tenant access denied")

    tenant = db.info.get("rls_tenant")
    if isinstance(tenant, dict) and tenant.get("bypass") != "on":
        # Keep later transactions (after a commit) on the same resolved context,
        # including an X-School-Id scope chosen by platform staff.
        tenant.update(
            school_id="" if school_id is None else str(school_id),
            user_id=str(user.id),
            role=role.value,
            membership_id="" if membership_id is None else str(membership_id),
            portals=",".join(portals),
        )

    if school_id is not None or portals:
        from sqlalchemy import text

        await db.execute(
            text("SELECT set_config('app.school_id', :v, true)"),
            {"v": "" if school_id is None else str(school_id)},
        )
        await db.execute(
            text("SELECT set_config('app.portals', :v, true)"),
            {"v": ",".join(portals)},
        )

    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=role,
        school_id=school_id,
        membership_id=membership_id,
        is_platform_admin=user.is_platform_admin,
        platform_role=user.platform_role or payload.get("platform_role"),
        portals=portals,
    )


def _finance_gate(roles: set[UserRole]) -> str | None:
    """Map a finance role gate to a permission code. School-admin-only gates stay role checks."""
    people = {UserRole.PARENT, UserRole.STUDENT, UserRole.TEACHER}
    staff = roles - people
    finance = {
        UserRole.SCHOOL_ADMIN,
        UserRole.ACCOUNTANT,
        UserRole.CASHIER,
        UserRole.AUDITOR,
        UserRole.FINANCE,
    }
    if not staff or not staff <= finance or staff == {UserRole.SCHOOL_ADMIN}:
        return None
    if UserRole.AUDITOR in staff:
        return "finance.read"
    if staff <= {UserRole.SCHOOL_ADMIN, UserRole.ACCOUNTANT, UserRole.FINANCE}:
        return "finance.manage"
    if UserRole.CASHIER in staff:
        return "finance.manage"
    return "finance.read"


def _perm_satisfies(perms: set[str], code: str) -> bool:
    if code in perms:
        return True
    if code == "finance.read" and "finance.manage" in perms:
        return True
    if code == "hr.read" and "hr.manage" in perms:
        return True
    if code == "payroll.read" and "payroll.approve" in perms:
        return True
    return False


def require_roles(*roles: UserRole):
    async def _dep(
        user: Annotated[CurrentUser, Depends(get_current_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> CurrentUser:
        role_ok = user.role in roles or (UserRole.PLATFORM_ADMIN in roles and user.is_platform_admin)
        code = _finance_gate(set(roles))
        if code is not None:
            from src.modules.access.catalog import permissions_for
            from src.modules.access.deps import effective_permissions

            perms = await effective_permissions(db, user)
            defaults = set(permissions_for(user.role))
            if role_ok:
                if code in defaults and not _perm_satisfies(perms, code):
                    raise ForbiddenError(f"Missing permission: {code}")
                return user
            if _perm_satisfies(perms, code) and code not in defaults:
                return user
            if code == "finance.read" and "finance.manage" in perms and "finance.manage" not in defaults:
                return user
        elif role_ok:
            return user
        if not role_ok:
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

    _dep.__roles__ = tuple(role.value for role in roles)
    return _dep


get_scoped_user = get_current_user


def require_super_admin(
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
) -> CurrentUser:
    from src.core.config import get_settings

    role_ok = user.platform_role == PlatformRole.SUPER_ADMIN.value
    email_ok = user.is_platform_admin and user.email.lower() == get_settings().superadmin_email.lower()
    if not (role_ok or email_ok):
        raise ForbiddenError("Super admin only")
    return user


require_super_admin.__super_admin__ = True


async def require_platform_member(
    user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """Platform console read access: any platform team member (is_platform_admin) or
    platform staff granted the ``platform`` portal. Writes keep stricter gates."""
    if user.role == UserRole.PLATFORM_ADMIN and user.is_platform_admin:
        return user
    if user.role == UserRole.PLATFORM_STAFF and "platform" in user.portals:
        return user
    raise ForbiddenError("This account has no access to the Platform portal")


require_platform_member.__roles__ = (UserRole.PLATFORM_ADMIN.value, UserRole.PLATFORM_STAFF.value)


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
