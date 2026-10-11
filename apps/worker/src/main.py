import json
import logging
import os
import time
import urllib.request

from src.core.config import get_settings
from src.core.redis import get_redis
from src.jobs import HANDLERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sybeez.worker")

QUEUE = "sybeez:jobs"
_last_release = 0.0


def _release_due() -> None:
    global _last_release
    now = time.time()
    if now - _last_release < 60:
        return
    _last_release = now
    settings = get_settings()
    url = f"{settings.core_api_url.rstrip('/')}/internal/v1/alerts/release-due"
    req = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json", "X-Internal-Token": settings.core_internal_token},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except Exception:
        logger.warning("Could not release scheduled alerts")


def _run_job(raw: str) -> None:
    job = json.loads(raw)
    kind = job.get("type")
    handler = HANDLERS.get(kind)
    if handler is None:
        logger.warning("Unknown job type: %s", kind)
        return
    handler(job.get("payload", {}))


def _jobs_backend() -> str:
    raw = os.environ.get("JOBS_BACKEND")
    if raw is not None and raw.strip():
        return raw.strip().lower()
    return (get_settings().jobs_backend or "redis").strip().lower() or "redis"


def _poll_sqs() -> None:
    import boto3

    settings = get_settings()
    url = (os.environ.get("JOBS_SQS_QUEUE_URL") or settings.jobs_sqs_queue_url or "").strip()
    if not url:
        raise SystemExit("JOBS_SQS_QUEUE_URL is required when JOBS_BACKEND=sqs")
    region = (os.environ.get("JOBS_SQS_REGION") or settings.jobs_sqs_region or "").strip() or None
    client = boto3.client("sqs", region_name=region)
    logger.info("Worker started, listening on SQS")
    while True:
        resp = client.receive_message(QueueUrl=url, MaxNumberOfMessages=1, WaitTimeSeconds=20)
        for message in resp.get("Messages") or []:
            try:
                _run_job(message.get("Body") or "{}")
                client.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
            except Exception:
                logger.exception("SQS job failed")
        _release_due()


def main() -> None:
    if _jobs_backend() == "sqs":
        _poll_sqs()
        return
    r = get_redis()
    logger.info("Worker started, listening on %s", QUEUE)
    while True:
        item = r.blpop(QUEUE, timeout=5)
        if not item:
            _release_due()
            continue
        _, raw = item
        try:
            _run_job(raw)
        except Exception:
            logger.exception("Job failed")
        time.sleep(0.01)
        _release_due()


if __name__ == "__main__":
    main()
