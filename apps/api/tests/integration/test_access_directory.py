from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_directory_search_scope_and_reset(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    r = await api.get("/api/v1/access/memberships?page_size=100", headers=w.admin.h)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    emails = {i["email"] for i in items}
    assert items and all(i["school_id"] == str(w.school_id) for i in items)  # nobody from the other school
    other_email = (await db_session.execute(text("SELECT email FROM users WHERE id = :u"), {"u": w.other_admin.user_id})).scalar_one()
    assert other_email not in emails
    # search by email fragment and by role
    mine = (await db_session.execute(text("SELECT email FROM users WHERE id = :u"), {"u": w.students[0].user_id})).scalar_one()
    q = await api.get(f"/api/v1/access/memberships?q={mine[:12]}", headers=w.admin.h)
    assert [i["email"] for i in q.json()["items"]] == [mine]
    roles = await api.get("/api/v1/access/memberships?role=teacher&page_size=100", headers=w.admin.h)
    assert {i["role"] for i in roles.json()["items"]} == {"TEACHER"} and roles.json()["total"] == 2
    # searching for another school's email finds nothing
    assert (await api.get(f"/api/v1/access/memberships?q={other_email}", headers=w.admin.h)).json()["total"] == 0
    # reset a student's login: one-time temp password, audited; the other school's admin cannot
    mem = next(i for i in items if i["email"] == mine)
    assert (await api.post(f"/api/v1/access/memberships/{mem['id']}/reset-login", headers=w.other_admin.h)).status_code in (403, 404)
    ok = await api.post(f"/api/v1/access/memberships/{mem['id']}/reset-login", headers=w.admin.h)
    assert ok.status_code == 200 and len(ok.json()["temporary_password"]) >= 10
    n = (await db_session.execute(text("SELECT count(*) FROM audit_logs WHERE school_id = :s AND action = 'login.reset'"), {"s": w.school_id})).scalar_one()
    assert n == 1
    # teachers/students cannot list the directory
    for p in (w.teacher, w.students[0], w.parent):
        assert (await api.get("/api/v1/access/memberships", headers=p.h)).status_code == 403
