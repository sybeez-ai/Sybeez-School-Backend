"""Lightweight telemetry hooks — extend with OpenTelemetry later."""

import logging

logger = logging.getLogger("sybeez.telemetry")


def track_event(name: str, **properties) -> None:
    logger.info("event=%s props=%s", name, properties)


def track_alert(name: str, **properties) -> None:
    """Ops hook. Scrape log lines with alert= for payroll run and bank-file failures."""
    logger.error("alert=%s event=%s props=%s", name, name, properties)
