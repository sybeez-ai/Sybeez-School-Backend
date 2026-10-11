"""Set the platform super admin and retire the previous one.

Run once from the release deploy, inside the API task:

    python -m src.modules.platform.provision_superadmin

The password is read from SUPERADMIN_INITIAL_PASSWORD or Secrets Manager
(sybeez-school/superadmin). It is never logged. Login in auth_mode=both uses
the local hash only when cognito_sub is empty, so this command stores a new
hash and clears that id. When a user pool is configured it also sets a
permanent Cognito password, which is what auth_mode=cognito checks.

admin@cybis.ai is disabled in the users table and deleted from the pool so it
cannot sign in to /platform. Startup bootstrap does not do this: it refuses to
replace a password that is already stored.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cognito import cognito_admin_denied, cognito_error_code, retire_cognito_user, set_cognito_password
from src.core.config import get_settings
from src.core.database import AsyncSessionLocal, engine
from src.core.secrets import resolve_superadmin_password, store_superadmin_password
from src.core.security import hash_password
from src.db.models import RefreshToken, User
from src.shared.enums import PlatformRole

logger = logging.getLogger(__name__)

# Previous platform login. Kept here only so each release deploy disables it.
RETIRED_SUPERADMIN_EMAIL = "admin@cybis.ai"


def apply_superadmin(user: User, *, password_hash: str) -> None:
    if not user.full_name:
        user.full_name = "Sybeez Super Admin"
    user.password_hash = password_hash
    user.cognito_sub = None
    user.is_platform_admin = True
    user.is_active = True
    user.status = "ACTIVE"
    user.platform_role = PlatformRole.SUPER_ADMIN.value


def apply_retirement(user: User) -> None:
    user.is_active = False
    user.is_platform_admin = False
    user.platform_role = None
    user.password_hash = None
    user.cognito_sub = None
    user.status = "DISABLED"


async def _user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(func.lower(User.email) == email.lower()))
    return result.scalar_one_or_none()


async def upsert_superadmin(db: AsyncSession, *, email: str, password: str) -> str:
    email = email.lower().strip()
    user = await _user_by_email(db, email)
    password_hash = hash_password(password)
    if user is None:
        user = User(
            email=email,
            full_name="Sybeez Super Admin",
            password_hash=password_hash,
            cognito_sub=None,
            is_platform_admin=True,
            is_active=True,
            status="ACTIVE",
            platform_role=PlatformRole.SUPER_ADMIN.value,
        )
        db.add(user)
        await db.flush()
        logger.info("Created super admin %s", email)
        return "created"
    apply_superadmin(user, password_hash=password_hash)
    await db.flush()
    logger.info("Reset super admin %s", email)
    return "reset"


async def retire_db_user(db: AsyncSession, *, email: str) -> str:
    email = email.lower().strip()
    user = await _user_by_email(db, email)
    if user is None:
        logger.info("Retired super admin %s is already absent from the database", email)
        return "missing"
    apply_retirement(user)
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked.is_(False))
        .values(revoked=True)
    )
    await db.flush()
    logger.info("Disabled super admin %s", email)
    return "retired"


async def provision() -> int:
    from_env = bool(os.environ.get("SUPERADMIN_INITIAL_PASSWORD", "").strip())
    password = resolve_superadmin_password()
    if not password:
        logger.error("Super admin password is not configured")
        return 1
    if from_env:
        store_superadmin_password(password)

    settings = get_settings()
    email = settings.superadmin_email.strip().lower()
    retired = RETIRED_SUPERADMIN_EMAIL.strip().lower()
    if retired == email:
        logger.error("Retired super admin email matches the active super admin")
        return 1

    async with AsyncSessionLocal() as db:
        db_state = await upsert_superadmin(db, email=email, password=password)
        retired_db = await retire_db_user(db, email=retired)
        await db.commit()

    cognito_ok = True
    try:
        cognito_state = set_cognito_password(email, password)
    except Exception as exc:
        code = cognito_error_code(exc)
        cognito_state = "denied" if cognito_admin_denied(exc) else "failed"
        cognito_ok = cognito_state != "failed"
        logger.error("Cognito password set failed for %s (%s)", email, code)
    try:
        retired_cognito = retire_cognito_user(retired)
    except Exception as exc:
        code = cognito_error_code(exc)
        retired_cognito = "denied" if cognito_admin_denied(exc) else "failed"
        if retired_cognito == "failed":
            cognito_ok = False
        logger.error("Cognito retire failed for %s (%s)", retired, code)

    logger.info(
        "Super admin provision finished email=%s db=%s cognito=%s retired_email=%s retired_db=%s retired_cognito=%s",
        email,
        db_state,
        cognito_state,
        retired,
        retired_db,
        retired_cognito,
    )
    return 0 if cognito_ok else 1


async def _run() -> int:
    try:
        return await provision()
    finally:
        await engine.dispose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    raise SystemExit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
