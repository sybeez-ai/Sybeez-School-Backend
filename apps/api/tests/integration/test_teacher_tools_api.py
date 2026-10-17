"""AI grading suggestions: nothing is recorded until a teacher approves (owner + sybeez_app)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.modules.teacher_tools import ai_client
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio
API = "/api/v1/teacher-tools"


@pytest.fixture
async def world(db_session):
    return await build_world(db_session)


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    async def call(user, path, payload):
        if path.endswith("/grade"):
            return {"suggested_marks": 7.5, "reasoning": "Covers two of three points.", "strengths": ["clear"],
                    "improve": ["add example"], "confidence": "medium"}
        return {"kind": payload["kind"], "title": "Fractions", "sections": [{"heading": "Objectives", "body": "x"}], "is_draft": True}

    monkeypatch.setattr(ai_client, "call", call)


async def _submission(db, w):
    hid, sid = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    await db.execute(
        text("""INSERT INTO homework (id, school_id, class_id, subject_id, teacher_id, title, status, max_marks,
                assigned_at, due_at, homework_type, allow_resubmission, created_at, updated_at)
                VALUES (:i, :s, :c, :sub, :t, 'Essay', 'PUBLISHED', 10, :a, :d, 'ASSIGNMENT', true, now(), now())"""),
        {"i": hid, "s": w.school_id, "c": w.class_id, "sub": w.subject_id, "t": w.teacher.profile_id, "a": now,
         "d": now + timedelta(days=1)},
    )
    await db.execute(
        text("""INSERT INTO homework_submissions (id, homework_id, student_id, school_id, submitted_at, content, status,
                created_at, updated_at) VALUES (:i, :h, :st, :s, now(), 'My answer', 'SUBMITTED', now(), now())"""),
        {"i": sid, "h": hid, "st": w.students[0].profile_id, "s": w.school_id},
    )
    await db.commit()
    return sid


async def test_draft_and_suggest_approve_flow(api, world, db_session):  # noqa: F811
    r = await api.post(f"{API}/draft", json={"kind": "lesson_plan", "topic": "Fractions"}, headers=world.teacher.h)
    assert r.status_code == 200, r.text
    assert (await api.post(f"{API}/draft", json={"kind": "lesson_plan", "topic": "Fractions"}, headers=world.students[0].h)).status_code == 403

    sid = await _submission(db_session, world)
    r = await api.post(f"{API}/grading/suggest", json={"submission_id": str(sid)}, headers=world.teacher.h)
    assert r.status_code == 201, r.text
    sug = r.json()
    assert sug["status"] == "SUGGESTED" and sug["suggested_marks"] == 7.5
    marks = (await db_session.execute(text("SELECT marks FROM homework_submissions WHERE id = :i"), {"i": sid})).scalar_one()
    assert marks is None  # a suggestion never records a mark

    # students and the other class's teacher cannot use or see it
    assert (await api.post(f"{API}/grading/suggest", json={"submission_id": str(sid)}, headers=world.students[0].h)).status_code == 403
    r = await api.post(f"{API}/grading/suggest", json={"submission_id": str(sid)}, headers=world.teacher2.h)
    assert r.status_code in (403, 404)

    r = await api.post(f"{API}/grading/{sug['id']}/approve", json={"final_marks": 8}, headers=world.teacher.h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "APPROVED" and r.json()["final_marks"] == 8
    marks = (await db_session.execute(text("SELECT marks FROM homework_submissions WHERE id = :i"), {"i": sid})).scalar_one()
    assert marks == 8
    assert (await api.post(f"{API}/grading/{sug['id']}/approve", json={}, headers=world.teacher.h)).status_code == 409


async def test_reject_keeps_marks_empty(api, world, db_session):  # noqa: F811
    sid = await _submission(db_session, world)
    sug = (await api.post(f"{API}/grading/suggest", json={"submission_id": str(sid)}, headers=world.teacher.h)).json()
    r = await api.post(f"{API}/grading/{sug['id']}/reject", headers=world.teacher.h)
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    marks = (await db_session.execute(text("SELECT marks FROM homework_submissions WHERE id = :i"), {"i": sid})).scalar_one()
    assert marks is None
