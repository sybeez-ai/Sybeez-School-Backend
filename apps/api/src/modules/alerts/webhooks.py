"""Meta and MSG91 webhook parsing. Signature checks use the raw body."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from src.modules.alerts.phone import is_opt_out_keyword, normalize_phone


@dataclass
class StatusUpdate:
    provider_message_id: str
    status: str
    error_code: str | None = None
    error_message: str | None = None
    recipient: str | None = None


@dataclass
class InboundUpdate:
    provider_message_id: str | None
    from_phone: str
    body: str
    opt_out: bool


def verify_meta_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    presented = header.split("=", 1)[1].strip()
    return hmac.compare_digest(digest, presented)


def verify_challenge(mode: str | None, token: str | None, expected: str) -> bool:
    return bool(expected) and mode == "subscribe" and token == expected


def parse_meta_webhook(payload: dict) -> tuple[list[StatusUpdate], list[InboundUpdate]]:
    statuses: list[StatusUpdate] = []
    inbound: list[InboundUpdate] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for item in value.get("statuses") or []:
                message_id = str(item.get("id") or "")
                if not message_id:
                    continue
                raw_status = str(item.get("status") or "").lower()
                mapped = _META_STATUS.get(raw_status)
                if mapped is None:
                    continue
                error_code = None
                error_message = None
                errors = item.get("errors") or []
                if errors:
                    error_code = str(errors[0].get("code") or "") or None
                    error_message = str(errors[0].get("title") or errors[0].get("message") or "") or None
                statuses.append(
                    StatusUpdate(
                        provider_message_id=message_id,
                        status=mapped,
                        error_code=error_code,
                        error_message=error_message,
                        recipient=str(item.get("recipient_id") or "") or None,
                    )
                )
            for item in value.get("messages") or []:
                body = _meta_inbound_body(item)
                phone = normalize_phone(str(item.get("from") or ""))
                if not phone:
                    continue
                inbound.append(
                    InboundUpdate(
                        provider_message_id=str(item.get("id") or "") or None,
                        from_phone=phone,
                        body=body,
                        opt_out=is_opt_out_keyword(body),
                    )
                )
    return statuses, inbound


def parse_msg91_webhook(payload: dict) -> list[StatusUpdate]:
    updates: list[StatusUpdate] = []
    reports = []
    if isinstance(payload.get("report"), list):
        request_id = str(payload.get("requestId") or payload.get("request_id") or "")
        for item in payload["report"]:
            reports.append((request_id, item))
    data = payload.get("data")
    if isinstance(data, list):
        for block in data:
            request_id = str(block.get("requestId") or block.get("request_id") or "")
            for item in block.get("report") or []:
                reports.append((request_id, item))
    if not reports and (payload.get("requestId") or payload.get("request_id") or payload.get("status")):
        reports.append((str(payload.get("requestId") or payload.get("request_id") or ""), payload))
    for request_id, item in reports:
        raw = str(item.get("status") or item.get("desc") or "")
        mapped = _MSG91_STATUS.get(raw.strip().upper()) or _MSG91_STATUS.get(raw.strip().lower())
        if mapped is None:
            continue
        message_id = str(item.get("requestId") or item.get("request_id") or request_id or "")
        if not message_id:
            continue
        updates.append(
            StatusUpdate(
                provider_message_id=message_id,
                status=mapped,
                error_code=str(item.get("errorCode") or item.get("error_code") or "") or None,
                error_message=str(item.get("desc") or item.get("failedReason") or "") or None,
                recipient=normalize_phone(str(item.get("number") or item.get("mobile") or "")) or None,
            )
        )
    return updates


def verify_msg91_token(presented: str | None, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(presented or "", expected)


def loads_json(raw: bytes) -> dict:
    data = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError("webhook body must be a JSON object")
    return data


_META_STATUS = {
    "sent": "SENT",
    "delivered": "DELIVERED",
    "read": "READ",
    "failed": "FAILED",
}

_MSG91_STATUS = {
    "1": "DELIVERED",
    "DELIVERED": "DELIVERED",
    "2": "FAILED",
    "FAILED": "FAILED",
    "REJECTED": "FAILED",
    "0": "SENT",
    "SENT": "SENT",
    "READ": "READ",
}


def _meta_inbound_body(item: dict) -> str:
    kind = item.get("type")
    if kind == "text":
        return str((item.get("text") or {}).get("body") or "")
    if kind == "button":
        return str((item.get("button") or {}).get("text") or "")
    if kind == "interactive":
        interactive = item.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return str(reply.get("title") or reply.get("id") or "")
    return kind or ""
