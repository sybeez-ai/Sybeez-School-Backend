"""Platform dashboard totals, School admins directory and activity must see across schools under forced RLS."""

import pytest

from src.core.security import create_access_token
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_platform_reads(api, db_session):  # noqa: F811
    from uuid import uuid4

    from src.db.models import User

    w = await build_world(db_session)
    sa = User(id=uuid4(), email=f"sa-{uuid4().hex[:8]}@example.test", full_name="Platform SA", is_platform_admin=True, platform_role="SUPER_ADMIN")
    db_session.add(sa)
    await db_session.commit()
    h = {"Authorization": "Bearer " + create_access_token(subject=sa.id, role="PLATFORM_ADMIN", extra={"platform_role": "SUPER_ADMIN"})}
    d = (await api.get("/api/v1/platform/dashboard", headers=h)).json()
    assert d["total_students"] >= 3 and d["total_teachers"] >= 1
    ra = await api.get(f"/api/v1/platform/admins?school_id={w.school_id}", headers=h)
    assert ra.status_code == 200, ra.text
    admins = ra.json()
    assert admins.get("total", admins) , admins
    assert admins["total"] >= 1
    assert (await api.get("/api/v1/platform/activity", headers=h)).status_code == 200
