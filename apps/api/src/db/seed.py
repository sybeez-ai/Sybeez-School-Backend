"""Seed platform admin + demo school admin for local development.

Usage: python -m src.db.seed
"""

from __future__ import annotations

import asyncio

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

PLATFORM_EMAIL = "admin@sybeez.com"
PLATFORM_PASSWORD = "ChangeMe123!"

SCHOOL_ADMIN_EMAIL = "admin@greenvalley.edu"
SCHOOL_ADMIN_PASSWORD = "SchoolAdmin123!"
SCHOOL_SLUG = "green-valley"


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
    await ensure_schema()

    async with AsyncSessionLocal() as db:
        platform = await upsert_user(
            db,
            email=PLATFORM_EMAIL,
            full_name="Sybeez Platform Admin",
            password=PLATFORM_PASSWORD,
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
            email=SCHOOL_ADMIN_EMAIL,
            full_name="Priya Nair",
            password=SCHOOL_ADMIN_PASSWORD,
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

        print("Seed complete.")
        print(f"  Platform admin : {PLATFORM_EMAIL} / {PLATFORM_PASSWORD}")
        print(f"  School         : {school.name} ({school.slug})")
        print(f"  School admin   : {SCHOOL_ADMIN_EMAIL} / {SCHOOL_ADMIN_PASSWORD}")
        print(f"  Platform user  : {platform.id}")
        print(f"  School id      : {school.id}")


if __name__ == "__main__":
    asyncio.run(seed())
