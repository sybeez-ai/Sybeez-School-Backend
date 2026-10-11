"""Idempotent super-admin bootstrap.

The email is SUPERADMIN_EMAIL (default admin@sybeez.ai). The password is never
hard-coded: it comes from SUPERADMIN_INITIAL_PASSWORD or Secrets Manager secret
sybeez-school/superadmin. Cognito mode creates the pool user and stores no
local password hash. Local mode stores an argon2 hash. An existing account is
not given a new password on later boots.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cognito import ensure_cognito_user
from src.core.config import get_settings
from src.core.secrets import resolve_superadmin_password
from src.core.security import hash_password
from src.db.models import User

logger = logging.getLogger(__name__)


async def bootstrap_superadmin(
    db: AsyncSession,
    *,
    email: str | None = None,
    password: str | None = None,
) -> User | None:
    settings = get_settings()
    email = (email or settings.superadmin_email).lower().strip()
    secret = password if password is not None else resolve_superadmin_password()
    if not secret:
        logger.info("Super admin bootstrap skipped; password is not configured")
        return None

    cognito = settings.auth_provider.strip().lower() == "cognito"
    if cognito:
        ensure_cognito_user(email, secret)

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            full_name="Sybeez Super Admin",
            password_hash=None if cognito else hash_password(secret),
            is_platform_admin=True,
            is_active=True,
            status="ACTIVE",
        )
        db.add(user)
        await db.flush()
        logger.info("Bootstrapped super admin %s", email)
        return user

    user.is_platform_admin = True
    user.is_active = True
    if user.status != "ACTIVE":
        user.status = "ACTIVE"
    if not cognito and not user.password_hash:
        user.password_hash = hash_password(secret)
    await db.flush()
    logger.info("Super admin %s already present", email)
    return user
