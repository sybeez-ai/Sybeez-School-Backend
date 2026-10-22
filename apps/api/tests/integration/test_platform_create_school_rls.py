"""Platform admin creates a school as sybeez_app (NOBYPASSRLS) with no school in the token.

Regression: subscriptions, memberships, and audit rows were inserted while
app.school_id was empty, so forced RLS rejected the provisioning transaction.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.api.dependencies import CurrentUser
from src.core.database import AsyncSessionLocal
from src.db.models import SchoolMembership, Subscription, User
from src.modules.platform.schemas import InvitationCreate, SchoolAdminCreate, SchoolCreate
from src.modules.platform.service import PlatformService
from src.shared.enums import UserRole

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _dispose_pool():
    yield
    from src.core.database import engine

    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_cognito(monkeypatch):
    from src.modules.auth import cognito

    monkeypatch.setattr(cognito, "cognito_configured", lambda: False)


async def _wipe(school_ids: list, user_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET session_replication_role = replica"))
        for sid in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": sid})
        for uid in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        await db.execute(text("SET session_replication_role = origin"))
        await db.commit()


async def test_platform_admin_creates_school_under_forced_rls():
    actor_id = uuid4()
    async with AsyncSessionLocal() as db:
        db.add(
            User(
                id=actor_id,
                email=f"{uuid4().hex}@rls-example.com",
                full_name="Platform Admin",
                is_active=True,
                is_platform_admin=True,
            )
        )
        await db.commit()

    actor = CurrentUser(
        id=actor_id,
        email="admin@rls-example.com",
        full_name="Platform Admin",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )
    school_ids: list = []
    user_ids: list = [actor_id]
    try:
        async with AsyncSessionLocal() as db:
            # Same context get_db builds from a platform admin token.
            db.info["rls_tenant"] = {
                "school_id": "",
                "user_id": str(actor_id),
                "role": "PLATFORM_ADMIN",
                "bypass": "off",
            }
            await db.execute(text("SET LOCAL ROLE sybeez_app"))
            svc = PlatformService(db)
            out = await svc.create_school(
                actor,
                SchoolCreate(
                    name=f"RLS School {uuid4().hex[:6]}",
                    admin=SchoolAdminCreate(
                        full_name="School Admin",
                        email=f"{uuid4().hex}@rls-example.com",
                        password="TempPass123!",
                    ),
                ),
            )
            school_ids.append(out.id)
            user_ids.append(out.admin_user_id)
            inv, _ = await svc.invite_school_admin(
                actor, out.id, InvitationCreate(email=f"{uuid4().hex}@rls-example.com")
            )
            assert inv.school_id == out.id
            sub = (await db.execute(select(Subscription).where(Subscription.school_id == out.id))).scalars().all()
            assert len(sub) == 1
            mem = (
                (await db.execute(select(SchoolMembership).where(SchoolMembership.school_id == out.id))).scalars().all()
            )
            assert [m.role for m in mem] == [UserRole.SCHOOL_ADMIN.value]
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids)
