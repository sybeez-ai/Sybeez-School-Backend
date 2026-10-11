"""Optional AWS Secrets Manager overlay. Environment variables win when set."""

from __future__ import annotations

import json
import os


def load_secret_map() -> dict[str, str]:
    arn = (os.environ.get("ALERTS_SECRETS_ARN") or os.environ.get("WHATSAPP_SECRETS_ARN") or "").strip()
    if not arn:
        return {}
    try:
        import boto3

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in data.items()}
