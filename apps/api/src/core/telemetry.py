"""Lightweight telemetry hooks — extend with OpenTelemetry later."""

import logging

logger = logging.getLogger("sybeez.telemetry")


def track_event(name: str, **properties) -> None:
    logger.info("event=%s props=%s", name, properties)
