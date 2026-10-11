"""HR admin routes honour membership grants without opening teacher self-service."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.catalog import permissions_for
from src.modules.access.deps import effective_permissions
from src.shared.enums import UserRole

HR_ADMIN_ROLES = {UserRole.SCHOOL_ADMIN, UserRole.HR, UserRole.HR_MANAGER, UserRole.PLATFORM_ADMIN}


async def require_hr_admin(
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentUser:
    write = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    code = "hr.manage" if write else "hr.read"
    perms = await effective_permissions(db, user)
    defaults = set(permissions_for(user.role))
    role_ok = user.role in HR_ADMIN_ROLES or user.is_platform_admin
    if role_ok:
        if code in defaults and code not in perms and not (code == "hr.read" and "hr.manage" in perms):
            raise ForbiddenError(f"Missing permission: {code}")
        if code in defaults or user.role == UserRole.SCHOOL_ADMIN or user.is_platform_admin:
            if code == "hr.read" or "hr.manage" in perms or "hr.read" in perms or user.role == UserRole.SCHOOL_ADMIN:
                if not (code in defaults and code not in perms and not (code == "hr.read" and "hr.manage" in perms)):
                    return user
        if code in perms or (code == "hr.read" and "hr.manage" in perms):
            return user
        raise ForbiddenError(f"Missing permission: {code}")
    if code in perms and code not in defaults:
        return user
    if code == "hr.read" and "hr.manage" in perms and "hr.manage" not in defaults:
        return user
    raise ForbiddenError("Requires HR access")
