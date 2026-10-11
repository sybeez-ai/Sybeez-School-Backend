from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_scoped_user
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.core.config import get_settings
from src.db.models import MembershipPermissionOverride, PlatformStaff, PlatformStaffPermission, PlatformStaffPortal
from src.modules.access.catalog import PORTALS, permissions_for
from src.shared.enums import UserRole


def public(fn):
    fn.__public__ = True
    return fn


def service_auth(fn):
    fn.__service_auth__ = True
    return fn


async def effective_permissions(db: AsyncSession, user: CurrentUser) -> set[str]:
    if user.role == UserRole.PLATFORM_ADMIN:
        return set(permissions_for(UserRole.PLATFORM_ADMIN))
    if user.role == UserRole.PLATFORM_STAFF:
        staff = (
            await db.execute(
                select(PlatformStaff).where(PlatformStaff.user_id == user.id, PlatformStaff.status == "ACTIVE")
            )
        ).scalar_one_or_none()
        if staff is None:
            return set()
        granted = set()
        portals = (
            await db.execute(select(PlatformStaffPortal.portal).where(PlatformStaffPortal.staff_id == staff.id))
        ).scalars()
        for portal in portals:
            granted |= set(PORTALS.get(portal, frozenset()))
        extras = (
            await db.execute(
                select(PlatformStaffPermission.permission_code).where(PlatformStaffPermission.staff_id == staff.id)
            )
        ).scalars()
        granted.update(extras)
        return granted
    granted = set(permissions_for(user.role))
    if user.membership_id is None:
        return granted
    result = await db.execute(
        select(MembershipPermissionOverride).where(
            MembershipPermissionOverride.membership_id == user.membership_id,
            MembershipPermissionOverride.school_id == user.school_id,
        )
    )
    for row in result.scalars().all():
        if row.effect == "GRANT":
            granted.add(row.permission_code)
        elif row.effect == "REVOKE":
            granted.discard(row.permission_code)
    return granted


def require_permission(code: str):
    async def _dep(
        user: Annotated[CurrentUser, Depends(get_scoped_user)],
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> CurrentUser:
        if code not in await effective_permissions(db, user):
            raise ForbiddenError(f"Missing permission: {code}")
        return user

    _dep.__permission_code__ = code
    return _dep


def require_super_admin():
    """Platform routes are limited to the bootstrapped super admin, not staff portal grants."""

    async def _dep(user: Annotated[CurrentUser, Depends(get_scoped_user)]) -> CurrentUser:
        email = get_settings().superadmin_email.lower()
        if not user.is_platform_admin or user.email.lower() != email:
            raise ForbiddenError("Platform APIs are limited to the super admin")
        return user

    _dep.__super_admin__ = True
    return _dep
