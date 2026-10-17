"""Substitute finder, workload and principal report through the real API (as owner and as sybeez_app)."""

from __future__ import annotations

from datetime import date, time, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401  (fixture)

pytestmark = pytest.mark.asyncio
API = "/api/v1/ops"


@pytest.fixture
async def world(db_session):
    return await build_world(db_session)


def _next_monday() -> date:
    d = date.today() + timedelta(days=1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


async def _entry(db, w, teacher, start=time(9, 0), end=time(9, 45)):
    eid = uuid4()
    await db.execute(
        text("""INSERT INTO timetable_entries (id, school_id, class_id, subject_id, teacher_id, day_of_week,
                starts_at, ends_at, created_at, updated_at)
                VALUES (:i, :s, :c, :sub, :t, 0, :a, :b, now(), now())"""),
        {"i": eid, "s": w.school_id, "c": w.class_id, "sub": w.subject_id, "t": teacher.profile_id, "a": start, "b": end},
    )
    return eid


async def test_report_workload_pdf(api, world):  # noqa: F811
    today = date.today()
    r = await api.get(f"{API}/principal-report", params={"year": today.year, "month": today.month}, headers=world.admin.h)
    assert r.status_code == 200, r.text
    assert {"attendance", "fees", "classes_needing_attention"} <= set(r.json())
    r = await api.get(f"{API}/workload", params={"start": str(today - timedelta(days=30)), "end": str(today)}, headers=world.admin.h)
    assert r.status_code == 200, r.text
    r = await api.get(f"{API}/principal-report.pdf", params={"year": today.year, "month": today.month}, headers=world.admin.h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    r = await api.get(f"{API}/principal-report", params={"year": today.year, "month": today.month}, headers=world.teacher.h)
    assert r.status_code == 403


async def test_substitute_flow(api, world, db_session):  # noqa: F811
    eid = await _entry(db_session, world, world.teacher)
    await db_session.commit()
    on = _next_monday()
    r = await api.get(f"{API}/substitutes/candidates", params={"teacher_id": str(world.teacher.profile_id), "on": str(on)}, headers=world.admin.h)
    assert r.status_code == 200, r.text
    lesson = next(x for x in r.json() if x["timetable_entry_id"] == str(eid))
    assert str(world.teacher2.profile_id) in {c["teacher_id"] for c in lesson["candidates"]}
    assert str(world.teacher.profile_id) not in {c["teacher_id"] for c in lesson["candidates"]}
    body = {"timetable_entry_id": str(eid), "date": str(on), "substitute_teacher_id": str(world.teacher2.profile_id)}
    assert (await api.post(f"{API}/substitutes", json=body, headers=world.teacher.h)).status_code == 403
    r = await api.post(f"{API}/substitutes", json=body, headers=world.admin.h)
    assert r.status_code == 201, r.text
    assert (await api.post(f"{API}/substitutes", json=body, headers=world.admin.h)).status_code in (409, 422)
    r = await api.get(f"{API}/substitutes", params={"on": str(on)}, headers=world.admin.h)
    assert len(r.json()) == 1
