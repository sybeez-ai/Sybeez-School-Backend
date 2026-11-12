"""Send a WhatsApp reply only when every switch allows it. There is no SMS path."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

GRAPH_VERSION = "v20.0"


@dataclass(frozen=True)
class SendDecision:
    allow: bool
    reason: str


def sending_decision(
    *,
    sending_enabled: bool,
    school_enabled: bool,
    configured: bool,
    may_notify: bool,
    rate_ok: bool,
    under_cap: bool,
) -> SendDecision:
    """Global WHATSAPP_SENDING_ENABLED is required. Default is off, and a school switch is required too."""
    if not may_notify:
        return SendDecision(False, "opted_out")
    if not sending_enabled:
        return SendDecision(False, "sending_disabled")
    if not school_enabled:
        return SendDecision(False, "school_disabled")
    if not configured:
        return SendDecision(False, "not_configured")
    if not rate_ok:
        return SendDecision(False, "rate_limited")
    if not under_cap:
        return SendDecision(False, "monthly_cap")
    return SendDecision(True, "ok")


def _digits(phone: str) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


def text_payload(to: str, body: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": _digits(to),
        "type": "text",
        "text": {"preview_url": False, "body": (body or "")[:4096]},
    }


def template_payload(to: str, template_name: str, language_code: str, components: list | None = None) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": _digits(to),
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components or [],
        },
    }


def graph_url(phone_number_id: str, version: str = GRAPH_VERSION) -> str:
    return f"https://graph.facebook.com/{version}/{phone_number_id}/messages"


def post_graph(url: str, access_token: str, body: dict, *, opener=None) -> str:
    """POST one message. Callers must already have decided that sending is allowed."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"whatsapp send failed: HTTP {exc.code}") from exc
    messages = payload.get("messages") or [{}]
    return str(messages[0].get("id") or "")


def deliver(decision: SendDecision, payload: dict, *, url: str, access_token: str, post=None) -> tuple[str, str | None]:
    """Return (status, provider message id). A disabled decision never calls Meta."""
    if not decision.allow:
        return "suppressed", None
    sender = post or (lambda body: post_graph(url, access_token, body))
    try:
        message_id = sender(payload)
    except Exception:
        return "failed", None
    return "sent", message_id or None
