"""Resolve the super-admin initial password without logging it.

SUPERADMIN_EMAIL comes from the environment. The password is read from
SUPERADMIN_INITIAL_PASSWORD when that is set (local development and tests),
otherwise from AWS Secrets Manager secret `sybeez-school/superadmin`
(override the id with SUPERADMIN_SECRET_ID). The secret may be a raw string
or a JSON object with SUPERADMIN_INITIAL_PASSWORD.
"""

from __future__ import annotations

import json
import logging
import os

from src.core.config import get_settings

logger = logging.getLogger(__name__)


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
