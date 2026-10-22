"""Two-layer feature restriction: platform (global and per school) and school admin, no override, no cross-school."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.core.security import create_access_token
from src.db.models import User
from src.modules.features import service
from src.modules.features.catalog import FEATURES, feature_for_path, is_exempt
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _super(db) -> dict[str, str]:
    u = User(id=uuid4(), email=f"super-{uuid4().hex[:8]}@example.test", full_name="Super", is_platform_admin=True,
             platform_role="SUPER_ADMIN")
    db.add(u)
    await db.flush()
    tok = create_access_token(subject=u.id, role="PLATFORM_ADMIN", school_id=None, extra={"platform_role": "SUPER_ADMIN"})
    return {"Authorization": f"Bearer {tok}"}


async def test_catalog_paths():
    assert feature_for_path("/online-tests/abc") == "online_tests"
    assert feature_for_path("/students") is None
    assert is_exempt("/auth/login") and is_exempt("/features/me") and not is_exempt("/fees/me")
    assert all(k.startswith("portal.") or f["prefixes"] for k, f in FEATURES.items())


async def test_platform_block_global_and_school(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    sup = await _super(db_session)
    await db_session.commit()
    url = "/api/v1/online-tests/me"
    before = (await api.get(url, headers=w.students[0].h)).status_code
    assert before != 403
    # Per-school block hits only that school.
    r = await api.put(f"/api/v1/platform/features/schools/{w.school_id}/online_tests", json={"enabled": False, "reason": "unpaid"}, headers=sup)
    assert r.status_code == 200, r.text
    blocked = await api.get(url, headers=w.students[0].h)
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "feature_disabled"
    other = await api.get(url, headers=w.other_admin.h)
    assert "turned off" not in other.text
    # Lifting restores.
    await api.put(f"/api/v1/platform/features/schools/{w.school_id}/online_tests", json={"enabled": True}, headers=sup)
    assert (await api.get(url, headers=w.students[0].h)).status_code == before
    # Global block hits both schools.
    await api.put("/api/v1/platform/features/global/online_tests", json={"enabled": False}, headers=sup)
    try:
        assert (await api.get(url, headers=w.students[0].h)).status_code == 403
        assert "turned off" in (await api.get(url, headers=w.other_admin.h)).text
    finally:
        await api.put("/api/v1/platform/features/global/online_tests", json={"enabled": True}, headers=sup)
    assert "turned off" not in (await api.get(url, headers=w.other_admin.h)).text


async def test_only_super_admin_sets_platform_rules(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    for p in (w.admin, w.teacher, w.students[0], w.parent):
        assert (await api.put("/api/v1/platform/features/global/fees", json={"enabled": False}, headers=p.h)).status_code == 403
        assert (await api.put(f"/api/v1/platform/features/schools/{w.school_id}/fees", json={"enabled": False}, headers=p.h)).status_code == 403
    assert (await api.get("/api/v1/platform/features", headers=w.admin.h)).status_code == 403


async def test_school_admin_switch_is_own_school_only_and_cannot_override(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    sup = await _super(db_session)
    await db_session.commit()
    # School A turns reading buddy off: A's student is blocked, B's admin map is untouched.
    r = await api.put("/api/v1/features/school/reading_buddy", json={"enabled": False}, headers=w.admin.h)
    assert r.status_code == 200, r.text
    mine = {f["key"]: f for f in (await api.get("/api/v1/features/me", headers=w.students[0].h)).json()}
    assert mine["reading_buddy"]["enabled"] is False
    theirs = {f["key"]: f for f in (await api.get("/api/v1/features/me", headers=w.other_admin.h)).json()}
    assert theirs["reading_buddy"]["enabled"] is True
    assert (await api.get("/api/v1/reading-buddy/me", headers=w.students[0].h)).status_code == 403
    # Other school's admin cannot see or change school A's switches (RLS + API scope).
    rows = (await db_session.execute(text("SELECT count(*) FROM school_feature_settings WHERE school_id = :s"), {"s": w.school_id})).scalar_one()
    assert rows == 1
    # Non-admins cannot change switches.
    for p in (w.teacher, w.students[0], w.parent):
        assert (await api.put("/api/v1/features/school/fees", json={"enabled": False}, headers=p.h)).status_code == 403
    # Platform locks fees for school A: school admin can neither re-enable nor change it.
    await api.put(f"/api/v1/platform/features/schools/{w.school_id}/fees", json={"enabled": False, "reason": "contract"}, headers=sup)
    for val in (True, False):
        r = await api.put("/api/v1/features/school/fees", json={"enabled": val}, headers=w.admin.h)
        assert r.status_code == 403 and r.json()["error"]["code"] == "feature_locked"
    admin_view = {f["key"]: f for f in (await api.get("/api/v1/features/school", headers=w.admin.h)).json()}
    assert admin_view["fees"]["locked_by"] == "platform" and admin_view["fees"]["reason"] == "contract"
    # Teachers/students are not told the reason.
    t_view = {f["key"]: f for f in (await api.get("/api/v1/features/me", headers=w.teacher.h)).json()}
    assert t_view["fees"]["enabled"] is False and t_view["fees"]["reason"] is None
    # School B is unaffected by A's platform lock.
    b_view = {f["key"]: f for f in (await api.get("/api/v1/features/school", headers=w.other_admin.h)).json()}
    assert b_view["fees"]["enabled"] is True


async def test_portal_switch_blocks_whole_portal_and_audits(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    assert (await api.put("/api/v1/features/school/portal.school", json={"enabled": False}, headers=w.admin.h)).status_code == 422
    r = await api.put("/api/v1/features/school/portal.parent", json={"enabled": False}, headers=w.admin.h)
    assert r.status_code == 200
    blocked = await api.get("/api/v1/parents/me", headers=w.parent.h)
    assert blocked.status_code == 403
    assert (await api.get("/api/v1/features/me", headers=w.parent.h)).status_code == 200  # can still learn why
    assert (await api.get("/api/v1/students", headers=w.admin.h)).status_code == 200
    n = (await db_session.execute(text("SELECT count(*) FROM audit_logs WHERE school_id = :s AND action = 'feature.school_switch'"), {"s": w.school_id})).scalar_one()
    assert n == 1
    assert (await api.put("/api/v1/features/school/nonsense", json={"enabled": False}, headers=w.admin.h)).status_code == 404


async def test_platform_changes_are_audited(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    sup = await _super(db_session)
    await db_session.commit()
    await api.put(f"/api/v1/platform/features/schools/{w.school_id}/bookstore", json={"enabled": False, "reason": "x"}, headers=sup)
    n = (await db_session.execute(text("SELECT count(*) FROM audit_logs WHERE details->>'school_id' = :s AND action = 'feature.platform_rule'"), {"s": str(w.school_id)})).scalar_one()
    assert n == 1
    service.invalidate()


async def test_per_staff_feature_limit(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    mid = {}
    for name, p in (("t1", w.teacher), ("t2", w.teacher2)):
        mid[name] = (await db_session.execute(
            text("SELECT id FROM school_memberships WHERE user_id = :u AND school_id = :s"),
            {"u": p.user_id, "s": w.school_id})).scalar_one()
    tok = {n: {"Authorization": "Bearer " + create_access_token(subject=p.user_id, role="TEACHER", school_id=w.school_id, membership_id=mid[n])}
           for n, p in (("t1", w.teacher), ("t2", w.teacher2))}
    url = "/api/v1/online-tests"
    base = (await api.get(url, headers=tok["t1"])).status_code
    assert base != 403
    r = await api.put(f"/api/v1/features/members/{mid['t1']}/online_tests", json={"enabled": False}, headers=w.admin.h)
    assert r.status_code == 200 and r.json()["off"] == ["online_tests"], r.text
    blocked = await api.get(url, headers=tok["t1"])
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "feature_disabled"
    assert (await api.get(url, headers=tok["t2"])).status_code == base  # colleague unaffected
    me = {f["key"]: f for f in (await api.get("/api/v1/features/me", headers=tok["t1"])).json()}
    assert me["online_tests"]["enabled"] is False
    # only the school admin, only in their own school, never themselves
    assert (await api.put(f"/api/v1/features/members/{mid['t2']}/fees", json={"enabled": False}, headers=w.teacher.h)).status_code == 403
    assert (await api.put(f"/api/v1/features/members/{mid['t2']}/fees", json={"enabled": False}, headers=w.other_admin.h)).status_code == 404
    assert (await api.get(f"/api/v1/features/members/{mid['t1']}", headers=w.other_admin.h)).status_code == 404
    admin_mid = (await db_session.execute(text("SELECT id FROM school_memberships WHERE user_id = :u"), {"u": w.admin.user_id})).scalar_one()
    assert (await api.put(f"/api/v1/features/members/{admin_mid}/fees", json={"enabled": False}, headers=w.admin.h)).status_code == 403
    # restoring
    await api.put(f"/api/v1/features/members/{mid['t1']}/online_tests", json={"enabled": True}, headers=w.admin.h)
    assert (await api.get(url, headers=tok["t1"])).status_code == base
    n = (await db_session.execute(text("SELECT count(*) FROM audit_logs WHERE school_id = :s AND action = 'feature.member_switch'"), {"s": w.school_id})).scalar_one()
    assert n == 2
