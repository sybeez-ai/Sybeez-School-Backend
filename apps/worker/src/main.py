import logging
import threading
import time
import urllib.request

from src.core.config import get_settings
from src.core.redis import get_redis
from src.queue import QUEUE, jobs_backend, poll_redis, poll_sqs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sybeez.worker")

_last_release = 0.0


def _release_due() -> None:
    """Ask the API to send alerts that were held for quiet hours. At most once a minute."""
    global _last_release
    now = time.time()
    if now - _last_release < 60:
        return
    _last_release = now
    settings = get_settings()
    base = (settings.core_api_url or "").rstrip("/")
    if not base:
        return
    request = urllib.request.Request(
        f"{base}/internal/v1/alerts/release-due",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": settings.core_internal_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            response.read()
    except Exception:
        logger.warning("Could not release scheduled alerts")


_last_online_tests = 0.0
ONLINE_TESTS_INTERVAL_SECONDS = 30


def _online_tests_tick() -> None:
    """Ask the API to finalise expired online-test attempts, close ended tests and send reminders."""
    global _last_online_tests
    now = time.time()
    if now - _last_online_tests < ONLINE_TESTS_INTERVAL_SECONDS:
        return
    _last_online_tests = now
    settings = get_settings()
    base = (settings.core_api_url or "").rstrip("/")
    if not base:
        return
    request = urllib.request.Request(
        f"{base}/internal/v1/online-tests/maintenance",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": settings.core_internal_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except Exception:
        logger.warning("Could not run online-test maintenance")


def redis_reachable() -> bool:
    try:
        return bool(get_redis().ping())
    except Exception:
        return False


def consume_forever(stop: threading.Event | None = None) -> None:
    settings = get_settings()
    backend = jobs_backend()
    if backend not in {"redis", "sqs"}:
        raise RuntimeError("JOBS_BACKEND must be redis or sqs")
    logger.info("Worker started, backend=%s queue=%s", backend, QUEUE if backend == "redis" else settings.sqs_queue_url)
    if backend == "sqs":
        import boto3

        client = boto3.client("sqs", region_name=settings.aws_region or None)
        while stop is None or not stop.is_set():
            try:
                poll_sqs(client, settings.sqs_queue_url.strip())
            except Exception:
                logger.exception("SQS poll failed")
                time.sleep(2)
            _release_due()
            _online_tests_tick()
        return

    client = get_redis()
    while stop is None or not stop.is_set():
        try:
            poll_redis(client, timeout=5)
        except Exception:
            logger.exception("Redis poll failed")
            time.sleep(2)
        _release_due()
        _online_tests_tick()


def main() -> None:
    import uvicorn

    settings = get_settings()
    if settings.db_iam_auth or (settings.database_url or "").strip():
        from src.core.database import get_engine

        get_engine()

    uvicorn.run(
        "src.asgi:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
