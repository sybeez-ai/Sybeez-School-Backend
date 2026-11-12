"""Out-of-band event logging into audit_logs: login failures, app errors, client errors.

Writes use their own short session so a failed request (rolled back) still leaves a trace.
The session runs as the SERVICE role with the row's school as tenant, which the audit_logs
policies admit (0042 school rows; 0061 platform rows without a school). Logging never raises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select

logger = logging.getLogger(__name__)

_MAX = 2000
_tasks: set[asyncio.Task] = set()


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value[:_MAX]
    if isinstance(value, dict):
        return {str(k)[:64]: _clip(v) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_clip(v) for v in value[:40]]
    return value


def _uuid(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


async def record_event(
    action: str,
    *,
    message: str | None = None,
    school_id: Any = None,
    actor_id: Any = None,
    actor_email: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    ip_address: str | None = None,
    details: dict | None = None,
) -> None:
    from src.core.database import AsyncSessionLocal
    from src.db.models import AuditLog, SchoolMembership, User

    sid = _uuid(school_id)
    aid = _uuid(actor_id)
    try:
        async with AsyncSessionLocal() as session:
            if (aid is None and actor_email) or (sid is None and aid is not None) or (sid is None and actor_email):
                # Read-only lookups (who failed, which school) run with the RLS read bypass.
                session.info["rls_tenant"] = {"school_id": "", "user_id": "", "role": "PLATFORM_ADMIN", "bypass": "on"}
                if aid is None and actor_email:
                    aid = (
                        await session.execute(select(User.id).where(User.email == actor_email.strip().lower()))
                    ).scalar_one_or_none()
                if sid is None and aid is not None:
                    sid = (
                        await session.execute(
                            select(SchoolMembership.school_id).where(SchoolMembership.user_id == aid).limit(1)
                        )
                    ).scalar_one_or_none()
                await session.commit()
            session.info["rls_tenant"] = {
                "school_id": "" if sid is None else str(sid),
                "user_id": "" if aid is None else str(aid),
                "role": "SERVICE",
                "bypass": "off",
            }
            session.add(
                AuditLog(
                    action=action[:128],
                    school_id=sid,
                    actor_id=aid,
                    resource_type=(resource_type or None) and resource_type[:64],
                    resource_id=(resource_id or None) and str(resource_id)[:64],
                    ip_address=(ip_address or None) and ip_address[:64],
                    message=(message or None) and message[:_MAX],
                    details=_clip(details) if details else None,
                )
            )
            await session.commit()
    except Exception:  # noqa: BLE001 - logging must never break the caller
        logger.warning("event log write failed for %s", action, exc_info=True)


def record_event_soon(action: str, **kwargs: Any) -> None:
    """Fire-and-forget variant for error paths that cannot await."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(record_event(action, **kwargs))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def client_ip(request: Any) -> str | None:
    try:
        fwd = request.headers.get("x-forwarded-for") or ""
        if fwd:
            return fwd.split(",")[0].strip()
        return request.client.host if request.client else None
    except Exception:  # noqa: BLE001
        return None
