import logging

logger = logging.getLogger("sybeez.worker.documents")


def handle(payload: dict) -> None:
    logger.info("Processing documents job: %s", payload)
