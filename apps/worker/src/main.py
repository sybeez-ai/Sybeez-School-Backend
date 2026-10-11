import logging
import threading
import time

from src.core.config import get_settings
from src.core.redis import get_redis
from src.queue import QUEUE, jobs_backend, poll_redis, poll_sqs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sybeez.worker")


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
        return

    client = get_redis()
    while stop is None or not stop.is_set():
        try:
            poll_redis(client, timeout=5)
        except Exception:
            logger.exception("Redis poll failed")
            time.sleep(2)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "src.asgi:app",
        host="0.0.0.0",
        port=8000,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
