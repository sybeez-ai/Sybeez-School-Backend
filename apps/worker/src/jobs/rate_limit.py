"""Per-provider send rate limit backed by Redis INCR."""

from __future__ import annotations

import time


def within_limit(count: int, limit_per_minute: int) -> bool:
    if limit_per_minute <= 0:
        return True
    return count <= limit_per_minute


def allow_provider_send(redis_client, provider: str, limit_per_minute: int, now: float | None = None) -> bool:
    if limit_per_minute <= 0:
        return True
    stamp = int((time.time() if now is None else now) // 60)
    key = f"rl:provider:{provider}:{stamp}"
    count = int(redis_client.incr(key))
    if count == 1:
        redis_client.expire(key, 70)
    return within_limit(count, limit_per_minute)
