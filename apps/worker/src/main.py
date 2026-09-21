import json
import logging
import time

from src.core.redis import get_redis
from src.jobs import HANDLERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sybeez.worker")

QUEUE = "sybeez:jobs"


def main() -> None:
    r = get_redis()
    logger.info("Worker started, listening on %s", QUEUE)
    while True:
        item = r.blpop(QUEUE, timeout=5)
        if not item:
            continue
        _, raw = item
        try:
            job = json.loads(raw)
            kind = job.get("type")
            handler = HANDLERS.get(kind)
            if handler is None:
                logger.warning("Unknown job type: %s", kind)
                continue
            handler(job.get("payload", {}))
        except Exception:
            logger.exception("Job failed: %s", raw)
        time.sleep(0.01)


if __name__ == "__main__":
    main()
