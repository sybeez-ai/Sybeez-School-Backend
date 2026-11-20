"""Forward a verified WhatsApp webhook to the API. The API does the work."""

from __future__ import annotations

import json
import logging
import urllib.request

from src.core.config import get_settings

logger = logging.getLogger("sybeez.worker.whatsapp")


def handle(payload: dict) -> None:
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return
    settings = get_settings()
    url = f"{settings.core_api_url.rstrip('/')}/internal/v1/whatsapp-assistant/inbound"
    request = urllib.request.Request(
        url,
        data=json.dumps({"body": body}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": settings.core_internal_token,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response.read()
    except Exception as exc:
        logger.warning("whatsapp assistant forward failed: %s", type(exc).__name__)
