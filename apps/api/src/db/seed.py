"""Optional demo school for local development. Never runs unless SEED_DEMO_DATA=true.

Demo passwords are read from DEMO_PLATFORM_PASSWORD and DEMO_SCHOOL_ADMIN_PASSWORD.
They are not stored in this file. The production super admin is bootstrapped
separately from SUPERADMIN_EMAIL and Secrets Manager, not from this seed.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select, text

from src.core.database import AsyncSessionLocal, engine
from src.core.security import hash_password
from src.db.base import Base
from src.db import models  # noqa: F401 — register all models
from src.db.models import (
    School,
    SchoolMembership,
    Subscription,
    User,
)
from src.shared.enums import MembershipStatus, SchoolStatus, SubscriptionStatus, UserRole
from src.shared.utils import utcnow

DEMO_PLATFORM_EMAIL = "admin@sybeez.com"
DEMO_SCHOOL_ADMIN_EMAIL = "admin@greenvalley.edu"
SCHOOL_SLUG = "green-valley"


def _enabled() -> bool:
    return os.environ.get("SEED_DEMO_DATA", "").strip().lower() in {"1", "true", "yes"}


async def ensure_schema() -> None:
    """Create full Core + AI schema (PostgreSQL + pgvector) if missing."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await conn.run_sync(Base.metadata.create_all)


async def upsert_user(
    db,
    *,
    email: str,
    full_name: str,
    password: str,
    is_platform_admin: bool = False,
) -> User:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if user:
        user.full_name = full_name
        user.password_hash = hash_password(password)
        user.is_active = True
        user.is_platform_admin = is_platform_admin
        return user
    user = User(
        email=email.lower(),
        full_name=full_name,
        password_hash=hash_password(password),
        is_platform_admin=is_platform_admin,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def seed() -> None:
    if not _enabled():
        raise SystemExit(
            "Refusing to load demo data. Set SEED_DEMO_DATA=true to seed a local development school."
        )
    platform_password = os.environ.get("DEMO_PLATFORM_PASSWORD", "").strip()
    school_password = os.environ.get("DEMO_SCHOOL_ADMIN_PASSWORD", "").strip()
    if not platform_password or not school_password:
        raise SystemExit(
            "DEMO_PLATFORM_PASSWORD and DEMO_SCHOOL_ADMIN_PASSWORD are required when SEED_DEMO_DATA=true."
        )

    await ensure_schema()

    async with AsyncSessionLocal() as db:
        platform = await upsert_user(
            db,
            email=DEMO_PLATFORM_EMAIL,
            full_name="Sybeez Platform Admin",
            password=platform_password,
            is_platform_admin=True,
        )

        result = await db.execute(select(School).where(School.slug == SCHOOL_SLUG))
        school = result.scalar_one_or_none()
        if school is None:
            school = School(
                name="Green Valley School",
                slug=SCHOOL_SLUG,
                code="GREENVAL",
                status=SchoolStatus.ACTIVE.value,
                country="India",
                city="Chennai",
                timezone="Asia/Kolkata",
            )
            db.add(school)
            await db.flush()
            db.add(
                Subscription(
                    school_id=school.id,
                    plan_code="standard",
                    status=SubscriptionStatus.TRIAL.value,
                    starts_at=utcnow(),
                )
            )
            await db.flush()

        school_admin = await upsert_user(
            db,
            email=DEMO_SCHOOL_ADMIN_EMAIL,
            full_name="Priya Nair",
            password=school_password,
            is_platform_admin=False,
        )

        membership = (
            await db.execute(
                select(SchoolMembership).where(
                    SchoolMembership.user_id == school_admin.id,
                    SchoolMembership.school_id == school.id,
                    SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            db.add(
                SchoolMembership(
                    user_id=school_admin.id,
                    school_id=school.id,
                    role=UserRole.SCHOOL_ADMIN.value,
                    status=MembershipStatus.ACTIVE.value,
                )
            )
        else:
            membership.status = MembershipStatus.ACTIVE.value

        await db.commit()

        print("Demo seed complete. Passwords were taken from the environment and are not printed.")
        print(f"  Demo platform admin email : {DEMO_PLATFORM_EMAIL}")
        print(f"  Demo school               : {school.name} ({school.slug})")
        print(f"  Demo school admin email   : {DEMO_SCHOOL_ADMIN_EMAIL}")
        print(f"  Platform user             : {platform.id}")
        print(f"  School id                 : {school.id}")
        print("  Production super admin is bootstrapped from SUPERADMIN_EMAIL, not this seed.")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
