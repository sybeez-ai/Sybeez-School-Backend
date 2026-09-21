import logging

logger = logging.getLogger("sybeez.worker.notifications")


def handle(payload: dict) -> None:
    logger.info("Processing notifications job: %s", payload)
