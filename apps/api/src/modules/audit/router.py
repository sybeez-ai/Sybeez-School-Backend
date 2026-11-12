from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import public, require_permission
from src.modules.audit.schemas import AuditLogOut
from src.modules.audit.service import AuditService
from src.shared.enums import PlatformRole, UserRole
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_school_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    page: PageQuery,
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    items, total = await AuditService(db).list_school(user.school_id, page)
    return Page(items=items, total=total, page=page.page, page_size=page.page_size)


@router.get("/health")
@public
async def audit_health():
    return {"module": "audit", "status": "ok"}


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
    school_id: UUID | None = Query(default=None),
    limit: int = Query(200, ge=1, le=500),
):
    return await AuditService(db).list_platform_activity(
        actor_id=actor.id,
        is_admin=(actor.platform_role in {PlatformRole.SUPER_ADMIN.value, PlatformRole.ADMIN.value}),
        school_id=school_id,
        limit=limit,
    )


# ── client (browser) errors → platform logs ──────────────────

import time as _time  # noqa: E402

from fastapi import Request  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from src.api.dependencies import get_current_user  # noqa: E402

_CLIENT_ERR_WINDOW: dict[str, list[float]] = {}


class ClientErrorIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    page: str | None = Field(default=None, max_length=300)
    source: str | None = Field(default=None, max_length=300)
    stack: str | None = Field(default=None, max_length=4000)
    portal: str | None = Field(default=None, max_length=32)
    kind: str | None = Field(default=None, max_length=32)


@router.post("/client-errors", status_code=202)
async def report_client_error(
    body: ClientErrorIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    """A signed-in browser reports a crash or failed screen; it shows in the platform Logs view."""
    from src.core.event_log import client_ip, record_event

    key = str(user.id)
    now = _time.monotonic()
    recent = [t for t in _CLIENT_ERR_WINDOW.get(key, []) if now - t < 60]
    if len(recent) >= 20:
        return {"accepted": False}
    recent.append(now)
    _CLIENT_ERR_WINDOW[key] = recent
    await record_event(
        "app.client_error",
        message=f"{body.page or 'web'}: {body.message}"[:500],
        school_id=user.school_id,
        actor_id=user.id,
        resource_type="web",
        resource_id=(body.page or "")[:64] or None,
        ip_address=client_ip(request),
        details={
            "source": "web",
            "kind": body.kind or "error",
            "portal": body.portal,
            "page": body.page,
            "script": body.source,
            "stack": body.stack,
            "role": user.role.value,
            "user_agent": (request.headers.get("user-agent") or "")[:200],
        },
    )
    return {"accepted": True}
