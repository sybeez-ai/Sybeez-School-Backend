import json
import logging
import os
import time

from src.core.redis import get_redis
from src.jobs import HANDLERS
from src.jobs.fee_reminders import run_scheduled

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("sybeez.worker")

QUEUE = "sybeez:jobs"


def main() -> None:
    r = get_redis()
    interval = int(os.getenv("FEE_REMINDER_INTERVAL_SECONDS", "86400"))
    last_fee_run = 0.0
    logger.info("Worker started, listening on %s (fee reminders every %ss)", QUEUE, interval)
    while True:
        now = time.time()
        if now - last_fee_run >= interval:
            last_fee_run = now
            try:
                run_scheduled()
            except Exception:
                logger.exception("Scheduled fee reminders failed")
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
