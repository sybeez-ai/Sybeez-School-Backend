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


def cognito_error_code(exc: Exception) -> str:
    """Stable Cognito error code. The message is omitted so a password cannot leak."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return str(code)
    return type(exc).__name__


def _is_user_not_found(exc: Exception) -> bool:
    code = cognito_error_code(exc)
    return code in {"UserNotFoundException", "UserNotFound"}


def _find_cognito_username(api: Any, pool_id: str, email: str) -> str | None:
    try:
        user = api.admin_get_user(UserPoolId=pool_id, Username=email)
        return str(user.get("Username") or email)
    except Exception as exc:
        if not _is_user_not_found(exc):
            raise
    try:
        listed = api.list_users(UserPoolId=pool_id, Filter=f'email = "{email}"', Limit=1)
    except Exception as exc:
        logger.info("Cognito list_users failed for %s (%s)", email, cognito_error_code(exc))
        return None
    users = listed.get("Users") or []
    if not users:
        return None
    username = str(users[0].get("Username") or "")
    return username or None


def set_cognito_password(email: str, password: str, client: Any | None = None) -> str:
    """Create the pool user if needed and set a permanent password.

    Returns ``created``, ``reset``, or ``skipped`` when no user pool is configured.
    Unlike :func:`ensure_cognito_user`, an existing user gets the new password.
    """
    settings = get_settings()
    pool_id = (settings.cognito_user_pool_id or "").strip()
    if not pool_id:
        logger.info("Cognito user pool is unset; local password only for %s", email)
        return "skipped"
    api = _client(client)
    email = email.strip().lower()
    username = _find_cognito_username(api, pool_id, email)
    created = username is None
    if created:
        api.admin_create_user(
            UserPoolId=pool_id,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        username = email
    api.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )
    try:
        api.admin_enable_user(UserPoolId=pool_id, Username=username)
    except Exception as exc:
        logger.info("Cognito enable for %s returned %s", email, cognito_error_code(exc))
    logger.info("Cognito password set for %s (%s)", email, "created" if created else "reset")
    return "created" if created else "reset"


def retire_cognito_user(email: str, client: Any | None = None) -> str:
    """Disable and delete a pool user. Returns ``deleted``, ``disabled``, ``missing``, or ``skipped``."""
    settings = get_settings()
    pool_id = (settings.cognito_user_pool_id or "").strip()
    if not pool_id:
        logger.info("Cognito user pool is unset; nothing to retire for %s", email)
        return "skipped"
    api = _client(client)
    email = email.strip().lower()
    username = _find_cognito_username(api, pool_id, email)
    if username is None:
        logger.info("Cognito user %s is already absent", email)
        return "missing"
    disabled = False
    try:
        api.admin_disable_user(UserPoolId=pool_id, Username=username)
        disabled = True
    except Exception as exc:
        logger.info("Cognito disable for %s returned %s", email, cognito_error_code(exc))
    try:
        api.admin_delete_user(UserPoolId=pool_id, Username=username)
    except Exception as exc:
        logger.info("Cognito delete for %s returned %s", email, cognito_error_code(exc))
        if disabled:
            logger.info("Cognito user %s is disabled", email)
            return "disabled"
        raise
    logger.info("Deleted Cognito user %s", email)
    return "deleted"


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
