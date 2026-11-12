"""Best-effort Expo push for online-test events.

The platform only stores device tokens today; this is the first sender. It never raises into a
request: in-app notifications are the source of truth and push is a nudge on top.
Disabled in development and tests.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.db.models import PushDevice
from src.modules.notifications.service import notification_fanout_scope

log = logging.getLogger(__name__)
EXPO_URL = "https://exp.host/--/api/v2/push/send"
_tasks: set[asyncio.Task] = set()


async def _post(messages: list[dict]) -> None:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for i in range(0, len(messages), 100):
                resp = await client.post(EXPO_URL, json=messages[i : i + 100])
                if resp.status_code >= 400:
                    log.warning("expo push rejected: %s", resp.status_code)
    except Exception:  # noqa: BLE001
        log.warning("expo push failed", exc_info=True)


async def send_push(
    db: AsyncSession,
    *,
    school_id: UUID,
    user_ids: list[UUID],
    title: str,
    body: str,
    data: dict | None = None,
) -> int:
    """Queue a push to every Expo device of the given users. Returns the number queued."""
    if not user_ids or get_settings().is_dev:
        return 0
    try:
        async with notification_fanout_scope(db, school_id):
            tokens = (
                await db.execute(
                    select(PushDevice.token).where(
                        PushDevice.user_id.in_(user_ids), PushDevice.provider == "expo"
                    )
                )
            ).scalars().all()
        messages = [
            {"to": t, "title": title, "body": body, "data": data or {}, "sound": "default"}
            for t in tokens
            if t.startswith(("ExponentPushToken", "ExpoPushToken"))
        ]
        if messages:
            task = asyncio.create_task(_post(messages))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
        return len(messages)
    except Exception:  # noqa: BLE001
        log.warning("push lookup failed", exc_info=True)
        return 0
