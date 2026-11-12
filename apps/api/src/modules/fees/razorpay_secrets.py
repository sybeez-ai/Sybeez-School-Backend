"""Razorpay and field-encryption secrets.

Environment variables win. When a name is unset, the value is read from
RAZORPAY_SECRETS_ARN (JSON) and then from the alerts secret map.
"""

from __future__ import annotations

import json
import os

_NAMES = (
    "RAZORPAY_KEY_ID",
    "RAZORPAY_KEY_SECRET",
    "RAZORPAY_WEBHOOK_SECRET",
    "FEE_FIELD_KEY",
    "FEE_KMS_KEY_ID",
    "RAZORPAY_FIELD_KEY",
)


def _secret_map() -> dict[str, str]:
    merged: dict[str, str] = {}
    arn = os.getenv("RAZORPAY_SECRETS_ARN", "").strip()
    if arn:
        try:
            import boto3

            raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, dict):
            merged.update({str(key): "" if value is None else str(value) for key, value in data.items()})
    try:
        from src.core.secrets import load_secret_map

        alerts = load_secret_map()
    except Exception:
        alerts = {}
    for name in _NAMES:
        if not merged.get(name) and alerts.get(name):
            merged[name] = alerts[name]
    return merged


def razorpay_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    return _secret_map().get(name, "").strip()
