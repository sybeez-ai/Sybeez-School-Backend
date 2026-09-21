import logging

logger = logging.getLogger("sybeez.worker.embeddings")


def handle(payload: dict) -> None:
    logger.info("Processing embeddings job: %s", payload)
