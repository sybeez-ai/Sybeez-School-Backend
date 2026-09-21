import logging

logger = logging.getLogger("sybeez.worker.emails")


def handle(payload: dict) -> None:
    logger.info("Processing emails job: %s", payload)
