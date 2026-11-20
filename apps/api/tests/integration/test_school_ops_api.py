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


async def test_substitute_whatsapp_is_flagged_and_opt_in(api, world, db_session, monkeypatch):  # noqa: F811
    """Covering teacher is messaged only with the school switch AND their own opt-in; nothing is really sent."""
    from sqlalchemy import select

    from src.db.models import NotificationDelivery, NotificationPreference, User
    from src.modules.alerts import service as alerts

    sent: list[dict] = []

    async def fake_enqueue(job_type, payload):
        sent.append(payload)

    monkeypatch.setattr(alerts, "enqueue_job", fake_enqueue)
    on = _next_monday()
    eid = await _entry(db_session, world, world.teacher)
    eid2 = await _entry(db_session, world, world.teacher, start=time(11, 0), end=time(11, 45))
    cover = await db_session.get(User, world.teacher2.user_id)
    cover.phone = "+919876543210"
    await db_session.commit()

    def body(e):
        return {"timetable_entry_id": str(e), "date": str(on), "substitute_teacher_id": str(world.teacher2.profile_id)}

    # school switch off → nothing queued
    assert (await api.post(f"{API}/substitutes", json=body(eid), headers=world.admin.h)).status_code == 201
    assert sent == []
    # school on but teacher has not opted in → nothing queued
    await db_session.execute(text("""INSERT INTO school_alert_settings (school_id, whatsapp_enabled, sms_enabled,
        quiet_hours_start, quiet_hours_end, timezone, default_locale, created_at, updated_at)
        VALUES (:s, true, false, '00:00', '00:00', 'Asia/Kolkata', 'en', now(), now())"""), {"s": world.school_id})
    await db_session.commit()
    assert (await api.post(f"{API}/substitutes", json=body(eid2), headers=world.admin.h)).status_code == 201
    assert sent == []
    # cancel and re-confirm after opting in
    covers = (await api.get(f"{API}/substitutes", params={"on": str(on)}, headers=world.admin.h)).json()
    for c in covers:
        assert (await api.delete(f"{API}/substitutes/{c['id']}", headers=world.admin.h)).status_code == 204
    db_session.add(NotificationPreference(school_id=world.school_id, user_id=world.teacher2.user_id,
                                          notification_type="SYSTEM", whatsapp_enabled=True))
    await db_session.commit()
    assert (await api.post(f"{API}/substitutes", json=body(eid), headers=world.admin.h)).status_code == 201
    rows = (await db_session.execute(select(NotificationDelivery).where(NotificationDelivery.event_key == "substitute_assigned"))).scalars().all()
    assert len(rows) == 1 and rows[0].channel == "WHATSAPP"
    assert "covering" in rows[0].body and len(sent) in (0, 1)  # queued (or held for quiet hours)
