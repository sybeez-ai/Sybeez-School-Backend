"""Credential presence for WhatsApp (Meta Cloud API).

Values come from the process environment or from Secrets Manager via Settings.
A blank value keeps the sender in mock mode. The admin status string is
"WhatsApp not configured" until every WhatsApp credential is present.
"""

from __future__ import annotations

from typing import Any

WHATSAPP_NOT_CONFIGURED = "WhatsApp not configured"
CONFIGURED = "configured"
# Process flag. Stays false until Silver enables parent WhatsApp and sets the phone number id.
WHATSAPP_OFF = "WhatsApp parent messaging is off"

WHATSAPP_FIELDS = (
    ("whatsapp_cloud_access_token", "WHATSAPP_ACCESS_TOKEN"),
    ("whatsapp_cloud_phone_number_id", "WHATSAPP_PHONE_NUMBER_ID"),
    ("whatsapp_business_account_id", "WHATSAPP_BUSINESS_ACCOUNT_ID"),
    ("whatsapp_app_secret", "WHATSAPP_APP_SECRET"),
    ("whatsapp_verify_token", "WHATSAPP_VERIFY_TOKEN"),
)

# Sending needs these. Template submission also needs the business account id.
WHATSAPP_SEND_FIELDS = ("whatsapp_cloud_access_token", "whatsapp_cloud_phone_number_id")
WHATSAPP_TEMPLATE_FIELDS = ("whatsapp_cloud_access_token", "whatsapp_business_account_id")


def _value(settings: Any, field: str) -> str:
    return str(getattr(settings, field, "") or "").strip()


def missing_names(settings: Any, fields: tuple[tuple[str, str], ...]) -> list[str]:
    return [env_name for field, env_name in fields if not _value(settings, field)]


def whatsapp_send_ready(settings: Any) -> bool:
    return all(_value(settings, field) for field in WHATSAPP_SEND_FIELDS)


def whatsapp_templates_ready(settings: Any) -> bool:
    return all(_value(settings, field) for field in WHATSAPP_TEMPLATE_FIELDS)


def parent_cloud_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "whatsapp_parent_cloud_enabled", False))


def whatsapp_sending_on(settings: Any) -> bool:
    """True when Silver has turned sending on, or the older parent-cloud flag is on."""
    return parent_cloud_enabled(settings) or bool(getattr(settings, "whatsapp_sending_enabled", False))


def whatsapp_admin_status(settings: Any) -> str:
    if not whatsapp_sending_on(settings):
        return WHATSAPP_OFF
    if missing_names(settings, WHATSAPP_FIELDS):
        return WHATSAPP_NOT_CONFIGURED
    return CONFIGURED


def effective_whatsapp_provider(settings: Any) -> str:
    provider = str(getattr(settings, "whatsapp_provider", "") or "mock").strip().lower() or "mock"
    if provider in {"meta", "whatsapp_cloud"}:
        provider = "meta_cloud"
    if provider == "meta_cloud" and (not whatsapp_sending_on(settings) or not whatsapp_send_ready(settings)):
        return "mock"
    return provider


def provider_status(settings: Any) -> dict:
    return {
        "whatsapp_status": whatsapp_admin_status(settings),
        "whatsapp_sending_enabled": bool(getattr(settings, "whatsapp_sending_enabled", False)),
        "whatsapp_provider": effective_whatsapp_provider(settings),
        "whatsapp_missing": missing_names(settings, WHATSAPP_FIELDS),
    }
