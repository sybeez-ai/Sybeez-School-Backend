import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from src.core.config import get_settings

logger = logging.getLogger("sybeez.worker.deliveries")


def _http_json(method: str, url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8") if hasattr(e, "read") else ""
        raise RuntimeError(f"HTTP {e.code}: {raw}") from e


def _post_result(
    delivery_id: str,
    *,
    status: str,
    provider_message_id: str | None,
    error: str | None,
    attempts: int,
) -> None:
    s = get_settings()
    url = f"{s.core_api_url.rstrip('/')}/internal/v1/deliveries/{delivery_id}/result"
    _http_json(
        "POST",
        url,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": s.core_internal_token,
        },
        body={
            "status": status,
            "provider_message_id": provider_message_id,
            "last_error": error,
            "attempts": attempts,
        },
    )


def _send_sms_msg91(to: str, body: str) -> str:
    """
    MSG91 minimal integration.
    - If MSG91_TEMPLATE_ID is set: uses Flow API (template-based).
    - Otherwise: uses legacy sendsms endpoint (may not work on all MSG91 accounts).
    Returns a provider request/message id string when available.
    """
    s = get_settings()
    if not s.msg91_auth_key:
        raise RuntimeError("MSG91_AUTH_KEY missing")

    mobiles = to.replace("+", "")
    if s.msg91_template_id:
        resp = _http_json(
            "POST",
            "https://api.msg91.com/api/v5/flow/",
            headers={"Content-Type": "application/json", "authkey": s.msg91_auth_key},
            body={
                "template_id": s.msg91_template_id,
                "sender": s.msg91_sender_id or None,
                "short_url": "0",
                "mobiles": mobiles,
                "VAR1": body,
            },
        )
        return str(resp.get("request_id") or resp.get("message") or "msg91_flow")

    query = {
        "authkey": s.msg91_auth_key,
        "mobiles": mobiles,
        "message": body,
    }
    if s.msg91_sender_id:
        query["sender"] = s.msg91_sender_id
    url = "https://api.msg91.com/api/v2/sendsms?" + urllib.parse.urlencode(query)
    resp = _http_json("GET", url, headers={})
    return str(resp.get("type") or resp.get("message") or "msg91")


def _send_whatsapp_meta(to: str, body: str) -> str:
    s = get_settings()
    if not s.whatsapp_cloud_access_token or not s.whatsapp_cloud_phone_number_id:
        raise RuntimeError("WHATSAPP_CLOUD credentials missing")

    url = f"https://graph.facebook.com/v20.0/{s.whatsapp_cloud_phone_number_id}/messages"
    resp = _http_json(
        "POST",
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {s.whatsapp_cloud_access_token}",
        },
        body={
            "messaging_product": "whatsapp",
            "to": to.replace("+", ""),
            "type": "text",
            "text": {"preview_url": False, "body": body},
        },
    )
    msg = (resp.get("messages") or [{}])[0]
    return str(msg.get("id") or "wa")


def handle(payload: dict) -> None:
    """Send one outbound delivery job (best-effort with basic retries)."""
    delivery_id = payload.get("delivery_id")
    channel = payload.get("channel")
    to = payload.get("to")
    body = payload.get("body")
    provider = (payload.get("provider") or "mock").lower().strip()

    if not delivery_id or not channel or not to or not body:
        logger.warning("Invalid delivery payload: %s", payload)
        return

    provider_message_id: str | None = None
    last_error: str | None = None

    for attempt in range(1, 4):
        try:
            if provider == "mock":
                logger.info("MOCK %s to=%s body=%s", channel, to, body)
                provider_message_id = f"mock_{delivery_id}_{attempt}"
            elif channel == "SMS" and provider == "msg91":
                provider_message_id = _send_sms_msg91(to, body)
            elif channel == "WHATSAPP" and provider in {"meta", "whatsapp_cloud"}:
                provider_message_id = _send_whatsapp_meta(to, body)
            else:
                raise RuntimeError(f"Unsupported provider={provider} channel={channel}")

            _post_result(
                delivery_id,
                status="SENT",
                provider_message_id=provider_message_id,
                error=None,
                attempts=attempt,
            )
            return
        except Exception as e:
            last_error = str(e)
            logger.exception("Delivery failed (attempt %s): %s", attempt, payload)

    # failed after retries
    try:
        _post_result(
            delivery_id,
            status="FAILED",
            provider_message_id=provider_message_id,
            error=last_error,
            attempts=3,
        )
    except Exception:
        logger.exception("Failed to report delivery result")

