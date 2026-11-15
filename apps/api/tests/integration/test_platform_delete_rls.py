"""Platform Super Admin deletes as sybeez_app with forced RLS and no school on the token.

The session starts the way a live platform request does: role PLATFORM_ADMIN,
empty school, bypass off. Delete school, remove admin, and delete plan must
turn bypass on and pin the school before they write.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text

from src.api.dependencies import CurrentUser
from src.core.database import AsyncSessionLocal
from src.db.models import PlatformPlan, School, SchoolMembership, User
from src.modules.platform.schemas import PlanCreate, SchoolAdminCreate, SchoolCreate
from src.modules.platform.saas_service import PlatformSaasService
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


def _actor(user_id) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        email=f"super-{user_id.hex[:8]}@rls-example.com",
        full_name="Platform Super Admin",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )


async def _as_platform(db, actor: CurrentUser) -> None:
    db.info["rls_tenant"] = {
        "school_id": "",
        "user_id": str(actor.id),
        "role": "PLATFORM_ADMIN",
        "bypass": "off",
    }
    await db.execute(text("SET LOCAL ROLE sybeez_app"))


async def _wipe(school_ids: list, user_ids: list, plan_ids: list) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET session_replication_role = replica"))
        for pid in plan_ids:
            await db.execute(text("DELETE FROM platform_plans WHERE id = :id"), {"id": pid})
        for sid in school_ids:
            await db.execute(text("DELETE FROM schools WHERE id = :id"), {"id": sid})
        for uid in user_ids:
            await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": uid})
        await db.execute(text("SET session_replication_role = origin"))
        await db.commit()


async def test_super_admin_removes_an_admin_deletes_the_school_and_deletes_a_plan_under_rls():
    actor_id = uuid4()
    school_ids: list = []
    user_ids: list = [actor_id]
    plan_ids: list = []
    actor = _actor(actor_id)
    try:
        async with AsyncSessionLocal() as db:
            db.add(
                User(
                    id=actor_id,
                    email=actor.email,
                    full_name=actor.full_name,
                    is_active=True,
                    is_platform_admin=True,
                    platform_role="SUPER_ADMIN",
                )
            )
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            school = await PlatformService(db).create_school(
                actor,
                SchoolCreate(
                    name=f"RLS Delete {uuid4().hex[:6]}",
                    admin=SchoolAdminCreate(
                        full_name="Temp Admin",
                        email=f"{uuid4().hex}@rls-example.com",
                        password="TempPass123!",
                    ),
                ),
            )
            school_ids.append(school.id)
            user_ids.append(school.admin_user_id)
            plan = await PlatformSaasService(db).create_plan(
                actor,
                PlanCreate(code=f"rls{uuid4().hex[:8]}", name="RLS Plan"),
            )
            plan_ids.append(plan.id)
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformService(db).remove_school_admin(actor, school.id, school.admin_user_id)
            membership = (
                await db.execute(
                    select(SchoolMembership).where(
                        SchoolMembership.school_id == school.id,
                        SchoolMembership.user_id == school.admin_user_id,
                    )
                )
            ).scalar_one()
            assert membership.status == "LEFT"
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformSaasService(db).delete_plan(actor, plan.id)
            plan_ids.remove(plan.id)
            assert await db.get(PlatformPlan, plan.id) is None
            await db.commit()

        async with AsyncSessionLocal() as db:
            await _as_platform(db, actor)
            await PlatformService(db).delete_school(actor, school.id, school.name)
            school_ids.remove(school.id)
            assert await db.get(School, school.id) is None
            await db.commit()
    finally:
        await _wipe(school_ids, user_ids, plan_ids)
