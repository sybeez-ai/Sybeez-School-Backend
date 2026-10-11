"""Cognito user bootstrap and password check for the super admin.

Used only when AUTH_PROVIDER=cognito. School users keep local password hashes.
"""

from __future__ import annotations

import logging
from typing import Any

from src.core.config import get_settings

logger = logging.getLogger(__name__)


def _client(existing: Any | None = None) -> Any:
    if existing is not None:
        return existing
    import boto3

    settings = get_settings()
    region = settings.cognito_region or settings.aws_region or None
    return boto3.client("cognito-idp", region_name=region)


def ensure_cognito_user(email: str, password: str, client: Any | None = None) -> None:
    """Create the super admin in the user pool if missing. Does not reset an existing password."""
    settings = get_settings()
    if not settings.cognito_user_pool_id or not settings.cognito_client_id:
        raise RuntimeError("COGNITO_USER_POOL_ID and COGNITO_CLIENT_ID are required when AUTH_PROVIDER=cognito")
    api = _client(client)
    try:
        api.admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=email)
        logger.info("Cognito super admin already exists")
        return
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code")
        if code != "UserNotFoundException" and type(exc).__name__ != "UserNotFoundException":
            raise
    api.admin_create_user(
        UserPoolId=settings.cognito_user_pool_id,
        Username=email,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    api.admin_set_user_password(
        UserPoolId=settings.cognito_user_pool_id,
        Username=email,
        Password=password,
        Permanent=True,
    )
    logger.info("Created Cognito super admin")


def cognito_password_ok(email: str, password: str, client: Any | None = None) -> bool:
    settings = get_settings()
    api = _client(client)
    try:
        api.admin_initiate_auth(
            UserPoolId=settings.cognito_user_pool_id,
            ClientId=settings.cognito_client_id,
            AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
        return True
    except Exception:
        logger.info("Cognito password check failed for %s", email)
        return False
