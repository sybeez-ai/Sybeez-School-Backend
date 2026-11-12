"""HR admin routes honour membership grants without opening staff self-service.

School admin, HR, and HR_MANAGER have hr.read / hr.manage by default. A school
admin can grant those codes to one person. Platform staff with the hrm portal
receive the same codes. STAFF and TEACHER stay on their own rows unless granted.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.catalog import permissions_for
from src.modules.access.deps import effective_permissions
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.shared.enums import UserRole

HR_ADMIN_ROLES = {UserRole.SCHOOL_ADMIN, UserRole.HR, UserRole.HR_MANAGER, UserRole.PLATFORM_ADMIN}


def _satisfies(perms: set[str], code: str) -> bool:
    if code in perms:
        return True
    return code == "hr.read" and "hr.manage" in perms


async def user_is_hr_admin(db: AsyncSession, user: CurrentUser, *, write: bool = False) -> bool:
    """True when this session may act on school HR data, not only its own rows."""
    code = "hr.manage" if write else "hr.read"
    perms = await effective_permissions(db, user)
    defaults = set(permissions_for(user.role))
    role_ok = user.role in HR_ADMIN_ROLES or user.is_platform_admin
    if role_ok:
        if code in defaults and not _satisfies(perms, code):
            return False
        return True
    if _satisfies(perms, code) and code not in defaults:
        return True
    if code == "hr.read" and "hr.manage" in perms and "hr.manage" not in defaults:
        return True
    return False


async def require_hr_admin(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if await user_is_hr_admin(db, user, write=write):
        return user
    code = "hr.manage" if write else "hr.read"
    raise ForbiddenError(f"Requires HR access ({code})")


async def require_hr_or_self(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    """HR admins, or a teacher/staff member limited to their own records by the service."""
    if user.role in SELF_SERVICE_ROLES:
        return user
    write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    if await user_is_hr_admin(db, user, write=write):
        return user
    raise ForbiddenError("Requires HR access")
