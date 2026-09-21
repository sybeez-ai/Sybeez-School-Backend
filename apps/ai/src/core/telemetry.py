import logging

logger = logging.getLogger("sybeez.ai.telemetry")


def track(name: str, **kwargs) -> None:
    logger.info("%s %s", name, kwargs)
