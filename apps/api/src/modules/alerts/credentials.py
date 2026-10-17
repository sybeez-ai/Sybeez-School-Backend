"""Credential presence for WhatsApp (Meta Cloud API).

Values come from the process environment or from Secrets Manager via Settings.
A blank value keeps the sender in mock mode. The admin status string is
"WhatsApp not configured" until every WhatsApp credential is present.
"""

from __future__ import annotations

from typing import Any

WHATSAPP_NOT_CONFIGURED = "WhatsApp not configured"
CONFIGURED = "configured"

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


def whatsapp_admin_status(settings: Any) -> str:
    if missing_names(settings, WHATSAPP_FIELDS):
        return WHATSAPP_NOT_CONFIGURED
    return CONFIGURED


def effective_whatsapp_provider(settings: Any) -> str:
    provider = str(getattr(settings, "whatsapp_provider", "") or "mock").strip().lower() or "mock"
    if provider in {"meta", "whatsapp_cloud"}:
        provider = "meta_cloud"
    if provider == "meta_cloud" and not whatsapp_send_ready(settings):
        return "mock"
    return provider


def provider_status(settings: Any) -> dict:
    return {
        "whatsapp_status": whatsapp_admin_status(settings),
        "whatsapp_provider": effective_whatsapp_provider(settings),
        "whatsapp_missing": missing_names(settings, WHATSAPP_FIELDS),
    }
