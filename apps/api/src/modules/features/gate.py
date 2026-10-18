"""Router-level dependency that enforces the feature switches on every API route."""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import bearer, get_current_user
from src.core.config import get_settings
from src.core.constants import API_V1_PREFIX
from src.core.database import get_db
from src.core.exceptions import AppError, ForbiddenError
from src.modules.features.catalog import FEATURES, PORTAL_FOR_ROLE, feature_for_path, is_exempt
from src.modules.features.service import access_for
from src.shared.enums import UserRole

PLATFORM_ROLES = {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_STAFF}


def disabled_error(label: str, locked_by: str | None) -> ForbiddenError:
    who = "Sybeez" if locked_by == "platform" else "your school admin"
    return ForbiddenError(f"{label} is turned off for your school by {who}.", code="feature_disabled")


async def feature_gate(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    path = request.url.path
    if path.startswith(API_V1_PREFIX):
        path = path[len(API_V1_PREFIX):]
    if is_exempt(path):
        return
    if creds is None and not request.cookies.get(get_settings().access_cookie_name):
        return  # public route: its own handler decides
    try:
        user = await get_current_user(request, creds, db)
    except AppError:
        return  # the route's own auth dependency raises the proper 401/403
    if user.role in PLATFORM_ROLES or user.school_id is None:
        return
    keys = [k for k in (feature_for_path(path), PORTAL_FOR_ROLE.get(user.role.value)) if k]
    if not keys:
        return
    a = await access_for(db, user.school_id, keys)
    if not a.enabled:
        label = FEATURES.get(a.key or "", {}).get("label", "This feature")
        raise disabled_error(label, a.locked_by)
