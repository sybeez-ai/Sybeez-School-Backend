import logging

logger = logging.getLogger("sybeez.worker.progress")


def handle(payload: dict) -> None:
    logger.info("Processing progress job: %s", payload)
