"""Per-user rate limit for payroll routes. Redis when it is up; fail open with an alert when it is not."""

from __future__ import annotations

import time

from fastapi import Request

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import AppError
from src.core.redis import get_redis
from src.core.telemetry import track_alert


async def payroll_rate_limit(request: Request, user: CurrentUser) -> None:
    limit = get_settings().payroll_rate_limit_per_minute
    if limit <= 0:
        return
    bucket = int(time.time() // 60)
    key = f"rl:payroll:{user.id}:{bucket}"
    try:
        redis = await get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 70)
    except Exception as exc:  # noqa: BLE001
        track_alert("payroll_rate_limit_unavailable", error=type(exc).__name__, path=request.url.path)
        return
    if int(count) > limit:
        raise AppError("Too many payroll requests. Try again in a minute.", code="rate_limited", status_code=429)


async def assistant_rate_limit(request: Request, user: CurrentUser) -> None:
    """Tighter limit for the payroll assistant. Fails open when Redis is down, same as the other payroll routes."""
    limit = get_settings().payroll_assistant_rate_limit_per_minute
    if limit <= 0:
        return
    bucket = int(time.time() // 60)
    key = f"rl:payroll:assistant:{user.id}:{bucket}"
    try:
        redis = await get_redis()
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 70)
    except Exception as exc:  # noqa: BLE001
        track_alert("payroll_assistant_rate_limit_unavailable", error=type(exc).__name__, path=request.url.path)
        return
    if int(count) > limit:
        raise AppError("Too many assistant messages. Try again in a minute.", code="rate_limited", status_code=429)
