"""Ask the API to generate payroll after a period's staff attendance is locked."""

import logging

from src.core.config import get_settings
from src.jobs.deliveries import _http_json

logger = logging.getLogger("sybeez.worker.payroll")


def handle(payload: dict) -> None:
    settings = get_settings()
    url = f"{settings.core_api_url.rstrip('/')}/internal/v1/payroll/generate"
    result = _http_json(
        "POST",
        url,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": settings.core_internal_token,
        },
        body={
            "school_id": payload["school_id"],
            "period_start": payload["period_start"],
            "period_end": payload["period_end"],
            "locked_by": payload["locked_by"],
            "source": payload.get("source") or "ATTENDANCE_LOCK",
        },
    )
    logger.info("payroll_generate %s", result.get("run_id"))
