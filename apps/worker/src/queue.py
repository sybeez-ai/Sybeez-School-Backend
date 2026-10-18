"""Redis or SQS job consumer. The JSON body matches API enqueue_job."""

from __future__ import annotations

import json
import logging

from src.core.config import get_settings
from src.jobs import HANDLERS

logger = logging.getLogger("sybeez.worker")

QUEUE = "sybeez:jobs"


def handle_body(raw: str) -> None:
    job = json.loads(raw)
    kind = job.get("type")
    handler = HANDLERS.get(kind)
    if handler is None:
        logger.warning("Unknown job type: %s", kind)
        return
    handler(job.get("payload") or {})


def poll_redis(client, *, timeout: int = 5) -> bool:
    import redis

    try:
        item = client.blpop(QUEUE, timeout=timeout)
    except redis.exceptions.TimeoutError:
        # The connection's read timeout fired while waiting on an empty queue. That is idle, not a failure.
        return False
    if not item:
        return False
    _, raw = item
    handle_body(raw)
    return True


def poll_sqs(client, queue_url: str, *, wait_seconds: int = 5) -> int:
    if not queue_url:
        raise RuntimeError("SQS_QUEUE_URL is required when JOBS_BACKEND=sqs")
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=wait_seconds,
        VisibilityTimeout=60,
    )
    messages = response.get("Messages") or []
    for message in messages:
        handle_body(message["Body"])
        client.delete_message(QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"])
    return len(messages)


def jobs_backend() -> str:
    return (get_settings().jobs_backend or "redis").strip().lower()
