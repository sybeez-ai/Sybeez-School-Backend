"""Public enquiry submissions: a few per address per ten minutes, plus a honeypot.

Redis is used when it is up. A single-process counter covers the same window
when Redis is down, so a burst still cannot fill the enquiry list.
"""

from __future__ import annotations

import time

_WINDOW_SECONDS = 600
_LIMIT = 8
_local: dict[str, tuple[int, int]] = {}


async def allow_public_enquiry(ip: str, slug: str) -> bool:
    bucket = int(time.time() // _WINDOW_SECONDS)
    identity = f"{slug}:{ip or 'unknown'}:{bucket}"
    try:
        from src.core.redis import get_redis

        redis = await get_redis()
        key = f"rl:admissions:{identity}"
        count = await redis.incr(key)
        if int(count) == 1:
            await redis.expire(key, _WINDOW_SECONDS + 60)
        return int(count) <= _LIMIT
    except Exception:
        previous = _local.get(identity)
        count = previous[1] + 1 if previous and previous[0] == bucket else 1
        _local[identity] = (bucket, count)
        if len(_local) > 5000:
            _local.clear()
            _local[identity] = (bucket, count)
        return count <= _LIMIT
