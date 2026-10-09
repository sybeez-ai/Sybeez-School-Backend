from __future__ import annotations

import json
from typing import Any

from src.core.redis import get_redis

QUEUE = "sybeez:jobs"


async def enqueue_job(job_type: str, payload: dict[str, Any]) -> None:
    """Push a job onto the worker queue."""
    r = await get_redis()
    await r.rpush(QUEUE, json.dumps({"type": job_type, "payload": payload}))

