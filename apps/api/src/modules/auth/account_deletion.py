"""Self-service account deletion (App Store 5.1.1(v) / Play account deletion)."""

from __future__ import annotations

import logging
import time
from uuid import UUID

import anyio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, UnauthorizedError
from src.core.redis import get_redis
from src.core.security import verify_password
from src.db.models import SchoolMembership, User
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.schemas import DeleteAccountRequest

logger = logging.getLogger(__name__)

ALLOWED_ROLES = {"STUDENT", "PARENT", "TEACHER", "STAFF"}
MAX_ATTEMPTS = 5
WINDOW_SECONDS = 900
RECENT_LOGIN_SECONDS = 600


class TooManyAttemptsError(AppError):
    def __init__(self) -> None:
        super().__init__("Too many attempts. Try again later.", code="too_many_attempts", status_code=429)


async def _check_budget(user_id: UUID) -> str:
    key = f"acct_delete_fail:{user_id}"
    try:
        r = await get_redis()
        used = int(await r.get(key) or 0)
    except Exception:  # noqa: BLE001 - Redis outage must not lock people out of deletion
        return key
    if used >= MAX_ATTEMPTS:
        raise TooManyAttemptsError()
    return key


async def _record_failure(key: str) -> None:
    try:
        r = await get_redis()
        if await r.incr(key) == 1:
            await r.expire(key, WINDOW_SECONDS)
    except Exception:  # noqa: BLE001
        pass


async def _clear_failures(key: str) -> None:
    try:
        r = await get_redis()
        await r.delete(key)
    except Exception:  # noqa: BLE001
        pass


async def _reauthenticate(user: User, body: DeleteAccountRequest, token_iat: int | None) -> str:
    """Return the method used (``password``, ``cognito`` or ``recent_login``) or raise 401."""
    if user.password_hash and not user.cognito_sub:
        if body.password and verify_password(body.password, user.password_hash):
            return "password"
        raise UnauthorizedError("Password is incorrect", code="invalid_password")
    if user.cognito_sub and cognito_svc.cognito_configured():
        if not body.password:
            raise UnauthorizedError("Password is incorrect", code="invalid_password")
        try:
            result = await anyio.to_thread.run_sync(cognito_svc.authenticate, user.email, body.password)
        except UnauthorizedError:
            raise UnauthorizedError("Password is incorrect", code="invalid_password") from None
        if isinstance(result, cognito_svc.CognitoNewPasswordChallenge):
            raise ConflictError("Set your new password before deleting the account", code="password_change_required")
        return "cognito"
    # No usable password (social sign-in): email confirmation plus a fresh session.
    fresh = token_iat is not None and (time.time() - token_iat) <= RECENT_LOGIN_SECONDS
    if body.confirm_email and body.confirm_email.lower() == user.email.lower() and fresh:
        return "recent_login"
    raise UnauthorizedError(
        "Confirm your email and sign in again, then retry within 10 minutes", code="reauth_required"
    )


async def delete_account(
    db: AsyncSession, *, user_id: UUID, body: DeleteAccountRequest, token_iat: int | None, ip: str | None
) -> UUID:
    user = (await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("Account not found")
    if user.is_platform_admin or user.platform_role:
        raise ConflictError("Platform accounts cannot be deleted from the app", code="account_deletion_not_allowed")
    roles = set(
        (
            await db.execute(
                select(SchoolMembership.role).where(
                    SchoolMembership.user_id == user_id, SchoolMembership.status == "ACTIVE"
                )
            )
        ).scalars()
    )
    if roles - ALLOWED_ROLES:
        raise ConflictError(
            "This account holds an administrative role. Ask your school or Sybeez support to remove it.",
            code="account_deletion_not_allowed",
        )

    budget_key = await _check_budget(user_id)
    try:
        method = await _reauthenticate(user, body, token_iat)
    except UnauthorizedError:
        await _record_failure(budget_key)
        raise
    await _clear_failures(budget_key)

    email = user.email
    # Disable first (reversible) so nothing changes in the database if Cognito refuses.
    cognito_status = await anyio.to_thread.run_sync(
        lambda: cognito_svc.disable_user_for_deletion(email=email)
    )
    try:
        # The function acts on app.user_id; pin it to the authenticated caller for this transaction.
        await db.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)})
        rid = (
            await db.execute(
                text("SELECT app.delete_my_account(:m, :c, :ip)"),
                {"m": method, "c": cognito_status, "ip": ip},
            )
        ).scalar_one()
        await db.commit()
    except Exception:
        await db.rollback()
        if cognito_status == "disabled":
            await anyio.to_thread.run_sync(lambda: cognito_svc.reenable_user(email=email))
        raise
    if cognito_status != "skipped":
        gone = await anyio.to_thread.run_sync(lambda: cognito_svc.delete_user_after_deletion(email=email))
        if not gone:
            logger.error("Cognito user for deleted account %s is disabled but not removed", user_id)
    return rid
