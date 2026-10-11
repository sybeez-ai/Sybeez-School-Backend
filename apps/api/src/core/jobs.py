from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.core.config import get_settings
from src.core.redis import get_redis

QUEUE = "sybeez:jobs"


def jobs_backend() -> str:
    raw = os.environ.get("JOBS_BACKEND")
    if raw is not None and raw.strip():
        return raw.strip().lower()
    return (get_settings().jobs_backend or "redis").strip().lower() or "redis"


async def enqueue_job(job_type: str, payload: dict[str, Any]) -> None:
    """Push a job onto the worker queue. JOBS_BACKEND=sqs uses SQS when configured."""
    if jobs_backend() == "sqs":
        await _enqueue_sqs(job_type, payload)
        return
    r = await get_redis()
    await r.rpush(QUEUE, json.dumps({"type": job_type, "payload": payload}))


async def _enqueue_sqs(job_type: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    url = (os.environ.get("JOBS_SQS_QUEUE_URL") or settings.jobs_sqs_queue_url or "").strip()
    if not url:
        raise RuntimeError("JOBS_SQS_QUEUE_URL is required when JOBS_BACKEND=sqs")
    body = json.dumps({"type": job_type, "payload": payload})
    region = (os.environ.get("JOBS_SQS_REGION") or settings.jobs_sqs_region or "").strip() or None

    def _send() -> None:
        import boto3

        boto3.client("sqs", region_name=region).send_message(QueueUrl=url, MessageBody=body)

    await asyncio.to_thread(_send)

