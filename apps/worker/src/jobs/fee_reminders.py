"""Scheduled fee reminders. Calls the core API internal endpoint."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from src.core.config import get_settings

logger = logging.getLogger("sybeez.worker.fee_reminders")


def _post(path: str) -> dict:
    settings = get_settings()
    url = f"{settings.core_api_url.rstrip('/')}{path}"
    req = urllib.request.Request(url, data=b"{}", method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Internal-Token", settings.core_internal_token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def handle(payload: dict) -> None:
    school_id = payload.get("school_id")
    if not school_id:
        logger.warning("fee_reminders job missing school_id")
        return
    result = _post(f"/internal/v1/fees/{school_id}/reminders/run")
    logger.info("Fee reminders for %s: %s", school_id, result)


def run_scheduled() -> None:
    """Daily sweep across every school. Invoked by the worker loop."""
    result = _post("/internal/v1/fees/reminders/run-all")
    logger.info("Scheduled fee reminders: schools=%s", result.get("schools"))
