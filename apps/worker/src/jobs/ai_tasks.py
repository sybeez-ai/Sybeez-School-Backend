import logging

logger = logging.getLogger("sybeez.worker.ai_tasks")


def handle(payload: dict) -> None:
    logger.info("Processing ai_tasks job: %s", payload)
