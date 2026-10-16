"""Online MCQ tests end to end through the real API (rolled-back transaction per test)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import Request
from sqlalchemy import text

from src.core.config import get_settings
from src.core.database import get_db, tenant_from_request
from src.main import app
from src.modules.fees.tenant import apply_tenant
from tests.integration.online_tests_world import World, build_world

pytestmark = pytest.mark.asyncio


@pytest.fixture(params=["superuser", "forced_rls"])
async def api(request, db_session):
    """Every test runs twice: once as the table owner, once as the production role (sybeez_app, forced RLS)."""
    rls = request.param == "forced_rls"

    async def _get_db(req: Request):
        if rls:
            db_session.info["rls_tenant"] = tenant_from_request(req)
            await db_session.execute(text("SET ROLE sybeez_app"))
            await apply_tenant(db_session)
        try:
            yield db_session
            await db_session.commit()
        finally:
            if rls:
                await db_session.execute(text("RESET ROLE"))

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://test"}) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)

API = "/api/v1"


@pytest.fixture
async def world(db_session) -> World:
    return await build_world(db_session)


def _iso(delta_minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()


QUESTIONS = [
    {"prompt": "2 + 2 = ?", "options": ["3", "4", "5", "6"], "correct_index": 1, "explanation": "Add.", "points": 2},
    {"prompt": "Capital of France?", "options": ["Paris", "Rome", "Oslo"], "correct_index": 0, "points": 1},
    {"prompt": "Largest planet?", "options": ["Mars", "Jupiter", "Venus", "Earth"], "correct_index": 1, "points": 1},
]


async def make_test(api, w: World, *, who=None, settings=None, questions=QUESTIONS, publish=True, class_id=None) -> dict:
    who = who or w.teacher
    body = {
        "class_id": str(class_id or w.class_id), "subject_id": str(w.subject_id), "title": "Unit test 1",
        "duration_minutes": 20, "opens_at": _iso(-1), "closes_at": _iso(120),
    }
    if settings:
        body["settings"] = settings
    r = await api.post(f"{API}/online-tests", json=body, headers=who.h)
    assert r.status_code == 201, r.text
    test = r.json()
    for q in questions:
        r = await api.post(f"{API}/online-tests/{test['id']}/questions", json=q, headers=who.h)
        assert r.status_code == 201, r.text
    if publish:
        r = await api.post(f"{API}/online-tests/{test['id']}/publish", headers=who.h)
        assert r.status_code == 200, r.text
        test = r.json()
    return test


async def start(api, w, test, student=None):
    student = student or w.students[0]
    return await api.post(f"{API}/online-tests/{test['id']}/attempts", json={}, headers=student.h)


def answer_all_correct(start_json: dict, test_detail: dict) -> dict:
    """Map question -> correct option text -> id as the student sees it."""
    by_prompt = {q["prompt"]: q for q in test_detail["questions"]}
    out = {}
    for q in start_json["questions"]:
        src = by_prompt[q["prompt"]]
        correct_text = next(o["text"] for o in src["options"] if o["id"] == src["correct_option_id"])
        out[q["id"]] = next(o["id"] for o in q["options"] if o["text"] == correct_text)
    return out


async def put_answers(api, student, attempt_id, mapping):
    for qid, oid in mapping.items():
        r = await api.put(f"{API}/online-test-attempts/{attempt_id}/answers/{qid}", json={"selected_option_id": oid}, headers=student.h)
        assert r.status_code == 200, r.text


async def expire(db, attempt_id):
    await db.execute(text("UPDATE online_test_attempts SET deadline_at = clock_timestamp() - interval '1 minute' WHERE id = :i"), {"i": attempt_id})


async def age_events(db, attempt_id):
    await db.execute(text("UPDATE online_test_events SET server_at = server_at - interval '10 seconds' WHERE attempt_id = :i"), {"i": attempt_id})


# ── authoring ────────────────────────────────────────────────


async def test_teacher_creates_and_publishes_and_totals(api, world):
    t = await make_test(api, world)
    assert t["status"] == "PUBLISHED" and t["total_points"] == 4 and t["question_count"] == 3
    assert t["questions"][0]["correct_option_id"]  # teacher sees the key


async def test_draft_not_visible_to_students_and_publish_validations(api, world):
    t = await make_test(api, world, publish=False)
    r = await api.get(f"{API}/online-tests/me", headers=world.students[0].h)
    assert r.json() == []
    empty = await make_test(api, world, questions=[], publish=False)
    r = await api.post(f"{API}/online-tests/{empty['id']}/publish", headers=world.teacher.h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "no_questions"
    r = await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)
    assert r.status_code == 200


async def test_question_validation(api, world):
    t = await make_test(api, world, questions=[], publish=False)
    bad = [
        {"prompt": "x", "options": ["a", "a"], "correct_index": 0},
        {"prompt": "x", "options": ["a", "b"], "correct_index": 2},
        {"prompt": "x", "options": ["a"], "correct_index": 0},
        {"prompt": " ", "options": ["a", "b"], "correct_index": 0},
    ]
    for q in bad:
        r = await api.post(f"{API}/online-tests/{t['id']}/questions", json=q, headers=world.teacher.h)
        assert r.status_code == 422, q


async def test_teacher_cannot_author_for_unassigned_class_or_edit_others(api, world):
    r = await api.post(
        f"{API}/online-tests",
        json={"class_id": str(world.class2_id), "subject_id": str(world.subject_id), "title": "x"},
        headers=world.teacher.h,
    )
    assert r.status_code in (403, 404)  # under RLS the other class is not even visible
    t = await make_test(api, world, publish=False)
    r = await api.patch(f"{API}/online-tests/{t['id']}", json={"title": "hijack"}, headers=world.teacher2.h)
    assert r.status_code in (403, 404)
    r = await api.get(f"{API}/online-tests/{t['id']}", headers=world.teacher2.h)
    assert r.status_code in (403, 404)  # drafts are private to the author (and admins)
    r = await api.get(f"{API}/online-tests/{t['id']}", headers=world.admin.h)
    assert r.status_code == 200


async def test_cross_school_isolation(api, world):
    t = await make_test(api, world)
    for who in (world.other_admin, world.other_teacher):
        r = await api.get(f"{API}/online-tests/{t['id']}", headers=who.h)
        assert r.status_code in (403, 404)
    r = await api.get(f"{API}/online-tests", headers=world.other_admin.h)
    assert r.json() == []


async def test_clone_copies_questions_and_keys(api, world):
    t = await make_test(api, world)
    r = await api.post(f"{API}/online-tests/{t['id']}/clone", headers=world.teacher.h)
    assert r.status_code == 201
    c = r.json()
    assert c["status"] == "DRAFT" and c["question_count"] == 3 and c["total_points"] == 4
    assert all(q["correct_option_id"] for q in c["questions"])


async def test_edit_locked_once_students_started(api, world):
    t = await make_test(api, world)
    assert (await start(api, world, t)).status_code == 200
    r = await api.post(f"{API}/online-tests/{t['id']}/questions", json=QUESTIONS[0], headers=world.teacher.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "test_locked"
    r = await api.delete(f"{API}/online-tests/{t['id']}", headers=world.teacher.h)
    assert r.status_code == 409


async def test_update_question_keeps_key_consistent(api, world):
    t = await make_test(api, world, publish=False)
    t = (await api.get(f"{API}/online-tests/{t['id']}", headers=world.teacher.h)).json()
    q = t["questions"][0]
    r = await api.patch(
        f"{API}/online-tests/{t['id']}/questions/{q['id']}",
        json={"options": ["10", "4", "7"], "correct_index": 1}, headers=world.teacher.h,
    )
    assert r.status_code == 200
    nq = next(x for x in r.json()["questions"] if x["id"] == q["id"])
    assert [o["text"] for o in nq["options"]] == ["10", "4", "7"]
    assert nq["correct_index"] == 1
    assert nq["options"][1]["id"] == q["options"][1]["id"]  # unchanged text keeps its id


# ── taking a test ────────────────────────────────────────────


async def test_student_never_receives_the_key_and_cannot_read_test_detail(api, world):
    t = await make_test(api, world)
    r = await start(api, world, t)
    assert r.status_code == 200
    raw = r.text.lower()
    assert "correct" not in raw and "explanation" not in raw
    s = r.json()
    assert len(s["questions"]) == 3 and s["resumed"] is False
    lst = await api.get(f"{API}/online-tests/me", headers=world.students[0].h)
    assert "correct" not in lst.text.lower()
    r = await api.get(f"{API}/online-tests/{t['id']}", headers=world.students[0].h)
    assert r.status_code == 403
    r = await api.get(f"{API}/online-tests/{t['id']}/attempts", headers=world.students[0].h)
    assert r.status_code == 403


async def test_full_flow_grade_hold_release_and_parent_view(api, world, db_session):
    t = await make_test(api, world)
    st = world.students[0]
    s = (await start(api, world, t)).json()
    mapping = answer_all_correct(s, t)
    first = next(iter(mapping))
    wrong_q = list(mapping)[0]
    # make one answer wrong: pick a different option for the first question
    q0 = next(q for q in s["questions"] if q["id"] == wrong_q)
    mapping[wrong_q] = next(o["id"] for o in q0["options"] if o["id"] != mapping[wrong_q])
    await put_answers(api, st, s["attempt_id"], mapping)
    del first
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=st.h)
    assert r.status_code == 200 and r.json()["status"] == "SUBMITTED"

    # results are held until the teacher releases them
    r = await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)
    assert r.json()["released"] is False and r.json()["score"] is None
    lst = (await api.get(f"{API}/online-tests/me", headers=st.h)).json()
    assert lst[0]["results_available"] is False and lst[0]["score"] is None
    r = await api.get(f"{API}/online-tests/children/{st.profile_id}", headers=world.parent.h)
    assert r.json()[0]["released"] is False and r.json()[0]["score"] is None

    # cannot release while still open
    r = await api.post(f"{API}/online-tests/{t['id']}/release-results", headers=world.teacher.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "still_open"
    assert (await api.post(f"{API}/online-tests/{t['id']}/close", headers=world.teacher.h)).status_code == 200
    r = await api.post(f"{API}/online-tests/{t['id']}/release-results", headers=world.teacher.h)
    assert r.status_code == 200 and r.json()["notified"] >= 1

    r = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)).json()
    assert r["released"] is True
    full = {q["prompt"]: q for q in t["questions"]}
    expected = sum(full[q["prompt"]]["points"] for q in r["questions"] if q["is_correct"])
    assert r["score"] == expected and r["max_score"] == 4
    assert any(q["is_correct"] is False for q in r["questions"])
    assert all(q["correct_option_id"] for q in r["questions"])
    pr = (await api.get(f"{API}/online-tests/children/{st.profile_id}", headers=world.parent.h)).json()
    assert pr[0]["released"] is True and pr[0]["score"] == expected
    # in-app notification created for the student and the parent
    n = (await db_session.execute(text(
        "SELECT count(*) FROM notifications WHERE category = 'ONLINE_TEST_RESULT' AND user_id IN (:s, :p)"
    ), {"s": st.user_id, "p": world.parent.user_id})).scalar()
    assert n == 2


async def test_one_attempt_only_and_other_student_unaffected(api, world):
    t = await make_test(api, world)
    s = (await start(api, world, t)).json()
    await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=world.students[0].h)
    r = await start(api, world, t)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_attempted"
    assert (await start(api, world, t, world.students[1])).status_code == 200
    # answers after submit are rejected
    q = s["questions"][0]
    r = await api.put(
        f"{API}/online-test-attempts/{s['attempt_id']}/answers/{q['id']}",
        json={"selected_option_id": q["options"][0]["id"]}, headers=world.students[0].h,
    )
    assert r.status_code == 409


async def test_student_in_other_class_or_other_student_cannot_touch_attempt(api, world):
    t = await make_test(api, world)
    r = await start(api, world, t, world.student_other_class)
    assert r.status_code == 404
    s = (await start(api, world, t)).json()
    q = s["questions"][0]
    r = await api.put(
        f"{API}/online-test-attempts/{s['attempt_id']}/answers/{q['id']}",
        json={"selected_option_id": q["options"][0]["id"]}, headers=world.students[1].h,
    )
    assert r.status_code == 404
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=world.students[1].h)
    assert r.status_code == 404
    r = await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=world.students[1].h)
    assert r.status_code == 404


async def test_resume_keeps_order_and_answers(api, world):
    t = await make_test(api, world)
    st = world.students[0]
    s1 = (await start(api, world, t)).json()
    q = s1["questions"][0]
    await put_answers(api, st, s1["attempt_id"], {q["id"]: q["options"][1]["id"]})
    s2 = (await start(api, world, t)).json()
    assert s2["resumed"] is True and s2["attempt_id"] == s1["attempt_id"]
    assert [x["id"] for x in s2["questions"]] == [x["id"] for x in s1["questions"]]
    assert [[o["id"] for o in x["options"]] for x in s2["questions"]] == [[o["id"] for o in x["options"]] for x in s1["questions"]]
    assert s2["answers"][q["id"]] == q["options"][1]["id"]
    assert s2["deadline_at"] == s1["deadline_at"]  # resuming never extends the clock


async def test_too_many_resumes_flags_the_attempt(api, world):
    t = await make_test(api, world, settings={"max_resumes": 1, "max_violations": 3})
    s = (await start(api, world, t)).json()
    for _ in range(2):
        assert (await start(api, world, t)).status_code == 200
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=world.students[0].h)
    assert r.json()["held_for_review"] is True


async def test_server_enforced_deadline(api, world, db_session):
    t = await make_test(api, world)
    st = world.students[0]
    s = (await start(api, world, t)).json()
    mapping = answer_all_correct(s, t)
    await put_answers(api, st, s["attempt_id"], mapping)
    await expire(db_session, s["attempt_id"])
    q = s["questions"][0]
    r = await api.put(
        f"{API}/online-test-attempts/{s['attempt_id']}/answers/{q['id']}",
        json={"selected_option_id": q["options"][0]["id"]}, headers=st.h,
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "attempt_expired"
    row = (await db_session.execute(text("SELECT status, submit_reason, score FROM online_test_attempts WHERE id = :i"), {"i": s["attempt_id"]})).one()
    assert row.status == "AUTO_SUBMITTED" and row.submit_reason == "TIME" and row.score == 4  # answers saved in time still count
    r = await start(api, world, t)
    assert r.status_code == 409 and r.json()["error"]["code"] == "already_attempted"


async def test_start_blocked_before_open_and_after_close(api, world):
    body = {"class_id": str(world.class_id), "subject_id": str(world.subject_id), "title": "Later",
            "duration_minutes": 10, "opens_at": _iso(60), "closes_at": _iso(180)}
    t = (await api.post(f"{API}/online-tests", json=body, headers=world.teacher.h)).json()
    await api.post(f"{API}/online-tests/{t['id']}/questions", json=QUESTIONS[0], headers=world.teacher.h)
    assert (await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)).status_code == 200
    r = await start(api, world, t)
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_open"
    lst = (await api.get(f"{API}/online-tests/me", headers=world.students[0].h)).json()
    assert lst[0]["effective_status"] == "UPCOMING"


async def test_deadline_is_capped_by_closing_time(api, world):
    body = {"class_id": str(world.class_id), "subject_id": str(world.subject_id), "title": "Short window",
            "duration_minutes": 60, "opens_at": _iso(-5), "closes_at": _iso(10)}
    t = (await api.post(f"{API}/online-tests", json=body, headers=world.teacher.h)).json()
    await api.post(f"{API}/online-tests/{t['id']}/questions", json=QUESTIONS[0], headers=world.teacher.h)
    await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)
    s = (await start(api, world, t)).json()
    deadline = datetime.fromisoformat(s["deadline_at"])
    assert deadline <= datetime.now(timezone.utc) + timedelta(minutes=10, seconds=5)


async def test_shuffling_produces_different_orders(api, world):
    many = [{"prompt": f"Q{i}", "options": ["a", "b", "c", "d"], "correct_index": 0} for i in range(12)]
    t = await make_test(api, world, questions=many)
    orders = set()
    for st in world.students:
        s = (await start(api, world, t, st)).json()
        orders.add(tuple(q["prompt"] for q in s["questions"]))
    assert len(orders) > 1
    t2 = await make_test(api, world, questions=many, settings={"shuffle_questions": False, "shuffle_options": False})
    s = (await start(api, world, t2)).json()
    assert [q["prompt"] for q in s["questions"]] == [f"Q{i}" for i in range(12)]


# ── violations ───────────────────────────────────────────────


async def test_violations_warn_twice_then_auto_submit_and_hold(api, world, db_session):
    t = await make_test(api, world)
    st = world.students[0]
    s = (await start(api, world, t)).json()
    mapping = answer_all_correct(s, t)
    await put_answers(api, st, s["attempt_id"], mapping)
    url = f"{API}/online-test-attempts/{s['attempt_id']}/events"
    r1 = (await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=st.h)).json()
    assert r1["violation_count"] == 1 and r1["warnings_left"] == 2 and r1["status"] == "IN_PROGRESS"
    # the same incident reported twice in a moment counts once
    r1b = (await api.post(url, json={"events": [{"type": "WINDOW_BLUR"}]}, headers=st.h)).json()
    assert r1b["violation_count"] == 1
    await age_events(db_session, s["attempt_id"])
    r2 = (await api.post(url, json={"events": [{"type": "FULLSCREEN_EXIT"}]}, headers=st.h)).json()
    assert r2["violation_count"] == 2 and r2["warnings_left"] == 1 and r2["status"] == "IN_PROGRESS"
    await age_events(db_session, s["attempt_id"])
    r3 = (await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=st.h)).json()
    assert r3["auto_submitted"] is True and r3["status"] == "AUTO_SUBMITTED"
    # held for review: no result shown, teacher is told
    res = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)).json()
    assert res["released"] is False and res["held_for_review"] is True
    rows = (await api.get(f"{API}/online-tests/{t['id']}/attempts", headers=world.teacher.h)).json()
    mine = next(r for r in rows if r["student_id"] == str(st.profile_id))
    assert mine["review_status"] == "PENDING" and mine["violation_count"] == 3
    notified = (await db_session.execute(text(
        "SELECT count(*) FROM notifications WHERE category = 'ONLINE_TEST_FLAGGED' AND user_id = :u"), {"u": world.teacher.user_id})).scalar()
    assert notified == 1
    detail = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}", headers=world.teacher.h)).json()
    assert [e["type"] for e in detail["events"] if e["counted"]] == ["TAB_HIDDEN", "FULLSCREEN_EXIT", "TAB_HIDDEN"]
    # events after the attempt closed are rejected
    r = await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=st.h)
    assert r.status_code == 409


async def test_teacher_review_accept_then_release_shows_result(api, world, db_session):
    t = await make_test(api, world)
    st = world.students[0]
    s = (await start(api, world, t)).json()
    url = f"{API}/online-test-attempts/{s['attempt_id']}/events"
    for _ in range(3):
        await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=st.h)
        await age_events(db_session, s["attempt_id"])
    await api.post(f"{API}/online-tests/{t['id']}/close", headers=world.teacher.h)
    await api.post(f"{API}/online-tests/{t['id']}/release-results", headers=world.teacher.h)
    res = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)).json()
    assert res["released"] is False  # released for the class, but this one is still held
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/review", json={"decision": "ACCEPT", "note": "ok"}, headers=world.teacher.h)
    assert r.status_code == 200 and r.json()["review_status"] == "CLEARED"
    res = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)).json()
    assert res["released"] is True


async def test_review_reopen_and_void(api, world, db_session):
    t = await make_test(api, world)
    st = world.students[0]
    s = (await start(api, world, t)).json()
    url = f"{API}/online-test-attempts/{s['attempt_id']}/events"
    for _ in range(3):
        await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=st.h)
        await age_events(db_session, s["attempt_id"])
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/review", json={"decision": "REOPEN", "extra_minutes": 10}, headers=world.teacher.h)
    assert r.status_code == 200 and r.json()["status"] == "IN_PROGRESS"
    again = (await start(api, world, t)).json()
    assert again["resumed"] is True and again["violation_count"] == 0
    await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=st.h)
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/review", json={"decision": "VOID"}, headers=world.teacher.h)
    assert r.json()["status"] == "VOID"
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/review", json={"decision": "ACCEPT"}, headers=world.teacher2.h)
    assert r.status_code in (403, 404)
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/review", json={"decision": "ACCEPT"}, headers=st.h)
    assert r.status_code == 403


async def test_secure_mode_off_logs_but_never_auto_submits(api, world):
    t = await make_test(api, world, settings={"secure_mode": False})
    s = (await start(api, world, t)).json()
    url = f"{API}/online-test-attempts/{s['attempt_id']}/events"
    r = (await api.post(url, json={"events": [{"type": "TAB_HIDDEN"}]}, headers=world.students[0].h)).json()
    assert r["violation_count"] == 0 and r["status"] == "IN_PROGRESS"


async def test_unknown_event_type_rejected(api, world):
    t = await make_test(api, world)
    s = (await start(api, world, t)).json()
    r = await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/events", json={"events": [{"type": "HACK"}]}, headers=world.students[0].h)
    assert r.status_code == 422


async def test_answer_validation(api, world):
    t = await make_test(api, world)
    s = (await start(api, world, t)).json()
    q = s["questions"][0]
    r = await api.put(f"{API}/online-test-attempts/{s['attempt_id']}/answers/{q['id']}", json={"selected_option_id": "nope"}, headers=world.students[0].h)
    assert r.status_code == 422
    r = await api.put(f"{API}/online-test-attempts/{s['attempt_id']}/answers/{q['id']}", json={"selected_option_id": None}, headers=world.students[0].h)
    assert r.status_code == 200  # clearing an answer is allowed


# ── roles / permissions ──────────────────────────────────────


async def test_role_gates(api, world):
    st, par = world.students[0], world.parent
    r = await api.post(f"{API}/online-tests", json={"class_id": str(world.class_id), "subject_id": str(world.subject_id), "title": "x"}, headers=st.h)
    assert r.status_code == 403
    r = await api.get(f"{API}/online-tests/me", headers=par.h)
    assert r.status_code == 403
    t = await make_test(api, world)
    r = await start(api, world, t, par)  # type: ignore[arg-type]
    assert r.status_code == 403
    # a parent only sees linked children
    r = await api.get(f"{API}/online-tests/children/{world.students[1].profile_id}", headers=par.h)
    assert r.status_code == 403
    r = await api.get(f"{API}/online-tests/children/{world.students[0].profile_id}", headers=world.parent_other.h)
    assert r.status_code == 403
    r = await api.get(f"{API}/online-tests/children", headers=par.h)
    assert [c["student_id"] for c in r.json()] == [str(world.students[0].profile_id)]
    # parents never get teacher endpoints
    assert (await api.get(f"{API}/online-tests", headers=par.h)).status_code == 403
    assert (await api.get(f"{API}/online-tests/{t['id']}", headers=par.h)).status_code == 403


async def test_admin_can_see_all_and_close(api, world):
    t = await make_test(api, world)
    r = await api.get(f"{API}/online-tests", headers=world.admin.h)
    assert [x["id"] for x in r.json()] == [t["id"]]
    r = await api.get(f"{API}/online-tests/{t['id']}/analytics", headers=world.admin.h)
    assert r.status_code == 200
    # a colleague assigned to a different class cannot read it
    assert (await api.get(f"{API}/online-tests/{t['id']}", headers=world.teacher2.h)).status_code in (403, 404)


# ── school switch and AI ─────────────────────────────────────


async def test_school_switch_disables_everything(api, world):
    t = await make_test(api, world)
    r = await api.put(f"{API}/online-tests/config", json={"enabled": False}, headers=world.teacher.h)
    assert r.status_code == 403
    r = await api.put(f"{API}/online-tests/config", json={"enabled": False}, headers=world.admin.h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    r = await api.post(f"{API}/online-tests", json={"class_id": str(world.class_id), "subject_id": str(world.subject_id), "title": "x"}, headers=world.teacher.h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "online_tests_disabled"
    assert (await api.get(f"{API}/online-tests/me", headers=world.students[0].h)).json() == []
    r = await start(api, world, t)
    assert r.status_code == 403
    r = await api.get(f"{API}/online-tests/config", headers=world.students[0].h)
    assert r.json()["enabled"] is False and r.json()["ai_monthly_cap"] == 0  # students never see the cap
    await api.put(f"{API}/online-tests/config", json={"enabled": True}, headers=world.admin.h)
    assert (await start(api, world, t)).status_code == 200


async def test_default_config_is_on(api, world):
    r = await api.get(f"{API}/online-tests/config", headers=world.teacher.h)
    assert r.json() == {"enabled": True, "ai_monthly_cap": 200, "ai_used_this_month": 0, "default_max_violations": 3}


def _fake_ai(monkeypatch, n=3, record=None):
    async def fake(user, payload):
        if record is not None:
            record.append(payload)
        return {
            "questions": [
                {"prompt": f"AI question {i}", "options": ["a", "b", "c", "d"], "correct_index": i % 4, "explanation": "because"}
                for i in range(n)
            ],
            "dropped_invalid": 1, "dropped_duplicates": 0,
        }

    monkeypatch.setattr("src.modules.online_tests.ai_client.generate_mcq_drafts", fake)


async def test_ai_drafts_need_review_before_publish_and_cap_is_enforced(api, world, monkeypatch):
    sent = []
    _fake_ai(monkeypatch, record=sent)
    t = await make_test(api, world, questions=[QUESTIONS[0]], publish=False)
    r = await api.post(f"{API}/online-tests/{t['id']}/ai-questions", json={"topic": "Fractions", "count": 3}, headers=world.teacher.h)
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["drafts"]) == 3 and out["used_this_month"] == 3 and out["monthly_cap"] == 200
    assert sent[0]["avoid_prompts"] == ["2 + 2 = ?"] and sent[0]["subject_name"] == "Maths"
    # drafts are NOT stored until the teacher saves them
    d = (await api.get(f"{API}/online-tests/{t['id']}", headers=world.teacher.h)).json()
    assert d["question_count"] == 1
    items = [dict(q, source="AI", reviewed=False) for q in out["drafts"]]
    r = await api.post(f"{API}/online-tests/{t['id']}/questions/bulk", json={"questions": items, "ai_generation_id": out["ai_generation_id"]}, headers=world.teacher.h)
    assert r.status_code == 201
    r = await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "ai_review_required"
    # editing one counts as review; confirm the rest
    qs = [q for q in r.json().get("questions", [])] or (await api.get(f"{API}/online-tests/{t['id']}", headers=world.teacher.h)).json()["questions"]
    ai_q = next(q for q in qs if q["source"] == "AI")
    r = await api.patch(f"{API}/online-tests/{t['id']}/questions/{ai_q['id']}", json={"prompt": "Edited AI question"}, headers=world.teacher.h)
    assert next(q for q in r.json()["questions"] if q["id"] == ai_q["id"])["source"] == "AI_EDITED"
    assert (await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)).status_code == 422
    assert (await api.post(f"{API}/online-tests/{t['id']}/questions/confirm-ai", headers=world.teacher.h)).status_code == 200
    assert (await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)).status_code == 200


async def test_ai_cap_blocks_when_exceeded(api, world, monkeypatch):
    _fake_ai(monkeypatch, n=2)
    await api.put(f"{API}/online-tests/config", json={"ai_monthly_cap": 3}, headers=world.admin.h)
    t = await make_test(api, world, questions=[], publish=False)
    ok = await api.post(f"{API}/online-tests/{t['id']}/ai-questions", json={"topic": "Fractions", "count": 2}, headers=world.teacher.h)
    assert ok.status_code == 200
    r = await api.post(f"{API}/online-tests/{t['id']}/ai-questions", json={"topic": "Fractions", "count": 2}, headers=world.teacher.h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "ai_cap_reached"
    cfg = (await api.get(f"{API}/online-tests/config", headers=world.teacher.h)).json()
    assert cfg["ai_used_this_month"] == 2 and cfg["ai_monthly_cap"] == 3


async def test_ai_only_for_test_owner_and_not_after_attempts(api, world, monkeypatch):
    _fake_ai(monkeypatch)
    t = await make_test(api, world)
    r = await api.post(f"{API}/online-tests/{t['id']}/ai-questions", json={"topic": "Fractions", "count": 2}, headers=world.teacher2.h)
    assert r.status_code in (403, 404)
    await start(api, world, t)
    r = await api.post(f"{API}/online-tests/{t['id']}/ai-questions", json={"topic": "Fractions", "count": 2}, headers=world.teacher.h)
    assert r.status_code == 409


# ── analytics / export / maintenance ────────────────────────


async def test_analytics_and_csv_export(api, world, db_session):
    t = await make_test(api, world)
    for i, st in enumerate(world.students[:2]):
        s = (await start(api, world, t, st)).json()
        m = answer_all_correct(s, t)
        if i == 1:
            qid = next(iter(m))
            q = next(q for q in s["questions"] if q["id"] == qid)
            m[qid] = next(o["id"] for o in q["options"] if o["id"] != m[qid])
        await put_answers(api, st, s["attempt_id"], m)
        await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=st.h)
    a = (await api.get(f"{API}/online-tests/{t['id']}/analytics", headers=world.teacher.h)).json()
    assert a["enrolled"] == 3 and a["started"] == 2 and a["submitted"] == 2
    assert a["highest"] == 100.0 and a["lowest"] < 100.0 and sum(a["distribution"]) == 2
    assert len(a["questions"]) == 3
    await db_session.execute(text("UPDATE users SET full_name = '=HYPERLINK(\"x\")' WHERE id = :u"), {"u": world.students[2].user_id})
    r = await api.get(f"{API}/online-tests/{t['id']}/results.csv", headers=world.teacher.h)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "NOT_STARTED" in r.text and "'=HYPERLINK" in r.text
    r = await api.get(f"{API}/online-tests/{t['id']}/results.csv", headers=world.students[0].h)
    assert r.status_code == 403


def _internal():
    return {"X-Internal-Token": get_settings().ai_service_token}


async def test_maintenance_finalises_expired_closes_and_auto_releases(api, world, db_session):
    t = await make_test(api, world, settings={"result_release": "AFTER_CLOSE"})
    st = world.students[0]
    s = (await start(api, world, t)).json()
    await put_answers(api, st, s["attempt_id"], answer_all_correct(s, t))
    r = await api.post("/internal/v1/online-tests/maintenance")
    assert r.status_code == 401
    # nothing to do while the test is live and the attempt is in time
    r = await api.post("/internal/v1/online-tests/maintenance", headers=_internal())
    assert r.status_code == 200 and r.json()["attempts_finalised"] == 0
    await expire(db_session, s["attempt_id"])
    await db_session.execute(text("UPDATE online_tests SET closes_at = clock_timestamp() - interval '30 seconds' WHERE id = :i"), {"i": t["id"]})
    r = await api.post("/internal/v1/online-tests/maintenance", headers=_internal())
    out = r.json()
    assert out["attempts_finalised"] == 1 and out["tests_closed"] == 1 and out["results_released"] == 1
    res = (await api.get(f"{API}/online-test-attempts/{s['attempt_id']}/result", headers=st.h)).json()
    assert res["released"] is True and res["score"] == 4
    again = (await api.post("/internal/v1/online-tests/maintenance", headers=_internal())).json()
    assert again == {"attempts_finalised": 0, "tests_closed": 0, "results_released": 0, "reminders": 0}


async def test_maintenance_sends_reminders_once(api, world, db_session):
    body = {"class_id": str(world.class_id), "subject_id": str(world.subject_id), "title": "Tomorrow",
            "duration_minutes": 10, "opens_at": _iso(30), "closes_at": _iso(300)}
    t = (await api.post(f"{API}/online-tests", json=body, headers=world.teacher.h)).json()
    await api.post(f"{API}/online-tests/{t['id']}/questions", json=QUESTIONS[0], headers=world.teacher.h)
    await api.post(f"{API}/online-tests/{t['id']}/publish", headers=world.teacher.h)
    out = (await api.post("/internal/v1/online-tests/maintenance", headers=_internal())).json()
    assert out["reminders"] == 1
    out = (await api.post("/internal/v1/online-tests/maintenance", headers=_internal())).json()
    assert out["reminders"] == 0
    n = (await db_session.execute(text("SELECT count(*) FROM notifications WHERE category = 'ONLINE_TEST_REMINDER'"))).scalar()
    assert n == len(world.students)


async def test_publish_notifies_students_and_parents(api, world, db_session):
    t = await make_test(api, world)
    rows = (await db_session.execute(text(
        "SELECT user_id FROM notifications WHERE category = 'ONLINE_TEST_PUBLISHED' AND entity_id = :e"), {"e": t["id"]})).scalars().all()
    expected = {p.user_id for p in world.students} | {world.parent.user_id}
    assert set(rows) == expected


async def test_audit_log_records_key_actions(api, world, db_session):
    t = await make_test(api, world)
    s = (await start(api, world, t)).json()
    await api.post(f"{API}/online-test-attempts/{s['attempt_id']}/submit", headers=world.students[0].h)
    actions = (await db_session.execute(text("SELECT action FROM online_test_audit_logs WHERE school_id = :s"), {"s": world.school_id})).scalars().all()
    for a in ("TEST_CREATED", "QUESTIONS_ADDED", "TEST_PUBLISHED", "ATTEMPT_STARTED", "ATTEMPT_SUBMITTED"):
        assert a in actions, a
    assert json.dumps(actions).count("correct") == 0
