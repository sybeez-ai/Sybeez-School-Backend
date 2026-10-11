"""Resolve secrets without logging them.

Super-admin: SUPERADMIN_EMAIL comes from the environment. The password is read from
SUPERADMIN_INITIAL_PASSWORD when that is set (local development and tests),
otherwise from AWS Secrets Manager secret `sybeez-school/superadmin`
(override the id with SUPERADMIN_SECRET_ID). The secret may be a raw string
or a JSON object with SUPERADMIN_INITIAL_PASSWORD.

WhatsApp and MSG91: `load_secret_map` reads ALERTS_SECRETS_ARN or
WHATSAPP_SECRETS_ARN. Settings copies a value only when the matching field is
still blank, so environment variables win.
"""

from __future__ import annotations

import json
import logging
import os

from src.core.config import get_settings

logger = logging.getLogger(__name__)


def load_secret_map() -> dict[str, str]:
    """WhatsApp and MSG91 credentials from Secrets Manager.

    Environment variables win: Settings only copies a key when its field is blank.
    No ARN means mock mode. A missing secret or a non-JSON payload returns an empty map.
    """
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


def password_from_secret_string(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(parsed, dict):
        value = parsed.get("SUPERADMIN_INITIAL_PASSWORD") or parsed.get("password")
        if isinstance(value, str) and value:
            return value
        return None
    if isinstance(parsed, str) and parsed:
        return parsed
    return None


def resolve_superadmin_password() -> str | None:
    """Return the initial password, or None when it is not configured.

    The value is never written to logs.
    """
    from_env = os.environ.get("SUPERADMIN_INITIAL_PASSWORD", "").strip()
    if from_env:
        logger.info("Super admin password loaded from SUPERADMIN_INITIAL_PASSWORD")
        return from_env

    settings = get_settings()
    secret_id = settings.superadmin_secret_id
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 is not installed; cannot read super admin secret %s", secret_id)
        return None

    try:
        client = boto3.client("secretsmanager", region_name=settings.aws_region or None)
        response = client.get_secret_value(SecretId=secret_id)
    except Exception:
        logger.warning("Super admin secret %s is not available; bootstrap skipped", secret_id)
        return None

    raw = response.get("SecretString") or ""
    password = password_from_secret_string(raw)
    if password:
        logger.info("Super admin password loaded from Secrets Manager secret %s", secret_id)
        return password
    logger.warning("Super admin secret %s did not contain SUPERADMIN_INITIAL_PASSWORD", secret_id)
    return None


def store_superadmin_password(password: str) -> bool:
    """Write the password to Secrets Manager. The value is never logged.

    Used when the one-off task received SUPERADMIN_INITIAL_PASSWORD so later
    boots can read the same secret. A missing permission is not fatal.
    """
    settings = get_settings()
    secret_id = settings.superadmin_secret_id
    try:
        import boto3
    except ImportError:
        logger.info("boto3 is not installed; left secret %s unchanged", secret_id)
        return False
    payload = json.dumps({"SUPERADMIN_INITIAL_PASSWORD": password})
    try:
        client = boto3.client("secretsmanager", region_name=settings.aws_region or None)
        client.put_secret_value(SecretId=secret_id, SecretString=payload)
    except Exception as exc:
        code = type(exc).__name__
        response = getattr(exc, "response", None)
        if isinstance(response, dict):
            code = str((response.get("Error") or {}).get("Code") or code)
        logger.info("Secrets Manager update for %s was not applied (%s)", secret_id, code)
        return False
    logger.info("Updated Secrets Manager secret %s", secret_id)
    return True
