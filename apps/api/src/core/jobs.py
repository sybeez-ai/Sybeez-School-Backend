from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.core.config import get_settings
from src.core.redis import get_redis

QUEUE = "sybeez:jobs"


def jobs_backend() -> str:
    """JOBS_BACKEND from the environment, otherwise the cached setting. Default redis."""
    raw = os.environ.get("JOBS_BACKEND")
    if raw is not None and raw.strip():
        return raw.strip().lower()
    return (get_settings().jobs_backend or "redis").strip().lower() or "redis"


def _sqs_client(settings):
    import boto3

    return boto3.client("sqs", region_name=settings.aws_region or None)


def send_sqs_message(settings, body: str) -> None:
    url = (settings.sqs_queue_url or "").strip()
    if not url:
        raise RuntimeError("SQS_QUEUE_URL is required when JOBS_BACKEND=sqs")
    _sqs_client(settings).send_message(QueueUrl=url, MessageBody=body)


async def enqueue_job(job_type: str, payload: dict[str, Any]) -> None:
    """Push a job onto the worker queue.

    JOBS_BACKEND=redis (default) uses RPUSH on sybeez:jobs.
    JOBS_BACKEND=sqs sends the same JSON body to SQS_QUEUE_URL.
    """
    settings = get_settings()
    backend = (settings.jobs_backend or "redis").strip().lower()
    body = json.dumps({"type": job_type, "payload": payload})
    if backend == "sqs":
        await asyncio.to_thread(send_sqs_message, settings, body)
        return
    if backend != "redis":
        raise RuntimeError("JOBS_BACKEND must be redis or sqs")
    client = await get_redis()
    await client.rpush(QUEUE, body)
