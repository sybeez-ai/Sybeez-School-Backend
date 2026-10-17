import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from src.core.config import get_settings
from src.core.redis import get_redis
from src.jobs.rate_limit import allow_provider_send
from src.jobs.whatsapp_providers import (
    DeliveryRejected,
    OutboundMessage,
    get_whatsapp_provider,
    resolve_outbound_provider,
)

logger = logging.getLogger("sybeez.worker.deliveries")


def _mask_phone(phone: str | None) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return ("*" * (len(digits) - 4)) + digits[-4:]


def _mask_text(value: str | None) -> str:
    import re

    return re.sub(r"\d{6,}", lambda match: _mask_phone(match.group(0)), value or "")


def _http_json(method: str, url: str, headers: dict[str, str], body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8") if hasattr(exc, "read") else ""
        error = RuntimeError(f"HTTP {exc.code}: {_mask_text(raw)}")
        error.error_code = _error_code(raw)  # type: ignore[attr-defined]
        raise error from exc


def _error_code(raw: str) -> str | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("code") is not None:
        return str(error["code"])
    return None


def _post_result(
    delivery_id: str,
    *,
    status: str,
    provider_message_id: str | None,
    error: str | None,
    attempts: int,
    error_code: str | None = None,
    scheduled_for: str | None = None,
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
            "error_code": error_code,
            "scheduled_for": scheduled_for,
        },
    )


def _send_whatsapp_meta(to: str, body: str) -> str:
    """Free-text sender. Callers must already be inside the 24-hour service window."""
    s = get_settings()
    if not s.whatsapp_cloud_access_token or not s.whatsapp_cloud_phone_number_id:
        raise DeliveryRejected("WhatsApp not configured", error_code="missing_credentials")

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


def _provider_key(channel: str, provider: str) -> str:
    if provider in {"meta", "whatsapp_cloud"}:
        return "meta_cloud"
    return provider


def _allow(channel: str, provider: str) -> bool:
    if provider == "mock":
        return True
    settings = get_settings()
    limit = settings.whatsapp_rate_limit_per_minute
    try:
        return allow_provider_send(get_redis(), _provider_key(channel, provider), limit)
    except Exception:
        logger.warning("provider rate limit unavailable provider=%s", provider)
        return True


def _schedule_retry(delivery_id: str, attempts: int) -> None:
    when = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    _post_result(
        delivery_id,
        status="SCHEDULED",
        provider_message_id=None,
        error="rate_limited",
        attempts=attempts,
        error_code="rate_limited",
        scheduled_for=when,
    )


def handle(payload: dict) -> None:
    """Send one outbound delivery job (best-effort with basic retries)."""
    delivery_id = payload.get("delivery_id")
    channel = payload.get("channel")
    to = payload.get("to")
    body = payload.get("body")
    provider = (payload.get("provider") or "mock").lower().strip()

    if not delivery_id or not channel or not to or body is None:
        logger.warning("Invalid delivery payload delivery_id=%s channel=%s", delivery_id, channel)
        return

    provider = _effective_provider(channel, provider)

    if not _allow(channel, provider):
        logger.info("rate limited channel=%s provider=%s to=%s", channel, provider, _mask_phone(to))
        try:
            _schedule_retry(delivery_id, int(payload.get("attempts") or 0))
        except Exception:
            logger.exception("Failed to schedule rate-limited delivery %s", delivery_id)
        return

    provider_message_id: str | None = None
    last_error: str | None = None
    error_code: str | None = None

    for attempt in range(1, 4):
        try:
            provider_message_id = _send(channel, provider, to, body, payload)
            _post_result(
                delivery_id,
                status="SENT",
                provider_message_id=provider_message_id,
                error=None,
                attempts=attempt,
            )
            return
        except Exception as exc:
            last_error = _mask_text(str(exc))
            error_code = getattr(exc, "error_code", None)
            logger.exception(
                "Delivery failed attempt=%s delivery_id=%s channel=%s to=%s",
                attempt,
                delivery_id,
                channel,
                _mask_phone(to),
            )
            if isinstance(exc, DeliveryRejected) and exc.error_code in {
                "outside_service_window",
                "template_required",
                "provider_stub",
                "missing_credentials",
            }:
                break

    try:
        _post_result(
            delivery_id,
            status="FAILED",
            provider_message_id=provider_message_id,
            error=last_error,
            attempts=3,
            error_code=error_code,
        )
    except Exception:
        logger.exception("Failed to report delivery result delivery_id=%s", delivery_id)


def _effective_provider(channel: str, provider: str) -> str:
    """Stay on mock when Meta credentials are absent. Never raises."""
    settings = get_settings()
    resolved = resolve_outbound_provider(
        channel,
        provider,
        access_token=settings.whatsapp_cloud_access_token,
        phone_number_id=settings.whatsapp_cloud_phone_number_id,
    )
    if resolved == "mock" and provider not in {"", "mock"}:
        if channel == "WHATSAPP":
            logger.warning("WhatsApp not configured")
    return resolved


def _send(channel: str, provider: str, to: str, body: str, payload: dict) -> str:
    if channel == "WHATSAPP":
        settings = get_settings()
        message = OutboundMessage(
            to=to,
            body=body,
            message_mode=payload.get("message_mode") or "text",
            template_name=payload.get("template_name"),
            language_code=payload.get("language_code"),
            components=payload.get("components") or [],
            service_window_open=bool(payload.get("service_window_open")),
        )
        if provider == "mock":
            logger.info("MOCK %s to=%s mode=%s", channel, _mask_phone(to), message.message_mode)
        sender = get_whatsapp_provider(
            provider,
            post_json=lambda url, headers, data: _http_json("POST", url, headers, data),
            phone_number_id=settings.whatsapp_cloud_phone_number_id,
            access_token=settings.whatsapp_cloud_access_token,
        )
        if provider in {"meta", "meta_cloud", "whatsapp_cloud"} and message.message_mode == "text":
            if not message.service_window_open:
                raise DeliveryRejected(
                    "WhatsApp free text is only allowed inside the 24-hour customer service window",
                    error_code="outside_service_window",
                )
            return _send_whatsapp_meta(to, body)
        return sender.send(message)

    if provider == "mock":
        logger.info("MOCK %s to=%s", channel, _mask_phone(to))
        return f"mock_{payload.get('delivery_id')}"
    raise RuntimeError(f"Unsupported provider={provider} channel={channel}")
