"""Exam prep: mock tests built from released MCQ questions, server-side grading, readiness, privacy."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import API as ROOT, api, make_test  # noqa: F401

pytestmark = pytest.mark.asyncio
EP = f"{ROOT}/exam-prep"
QS = [{"prompt": f"Q{i}: pick B", "options": ["A", "B", "C"], "correct_index": 1, "points": 1, "explanation": f"B is right {i}"} for i in range(6)]


async def _released_pool(api, w):  # noqa: F811
    t = await make_test(api, w, questions=QS)
    assert (await api.post(f"{ROOT}/online-tests/{t['id']}/close", headers=w.teacher.h)).status_code == 200
    assert (await api.post(f"{ROOT}/online-tests/{t['id']}/release-results", headers=w.teacher.h)).status_code in (200, 201)
    return t


async def test_mock_flow_grading_readiness_and_privacy(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    stu = w.students[0]
    # nothing released yet
    assert (await api.get(f"{EP}/subjects", headers=stu.h)).json() == []
    assert (await api.post(f"{EP}/mocks", json={"subject_id": str(w.subject_id), "question_count": 5}, headers=stu.h)).status_code == 409
    await _released_pool(api, w)
    subs = (await api.get(f"{EP}/subjects", headers=stu.h)).json()
    assert subs[0]["questions"] == 6
    # other-class student has no pool; parents/teachers cannot start
    assert (await api.get(f"{EP}/subjects", headers=w.student_other_class.h)).json() == []
    assert (await api.post(f"{EP}/mocks", json={"subject_id": str(w.subject_id)}, headers=w.parent.h)).status_code == 403
    assert (await api.get(f"{EP}/readiness", headers=w.teacher.h)).status_code == 403
    r = await api.post(f"{EP}/mocks", json={"subject_id": str(w.subject_id), "question_count": 5, "minutes": 10}, headers=stu.h)
    assert r.status_code == 201, r.text
    mock = r.json()
    assert len(mock["questions"]) == 5 and mock["review"] is None and 0 < mock["seconds_left"] <= 600
    assert "correct_option_id" not in str(mock) and "explanation" not in str(mock)
    # resume returns the same mock
    again = await api.post(f"{EP}/mocks", json={"subject_id": str(w.subject_id), "question_count": 5}, headers=stu.h)
    assert again.json()["id"] == mock["id"]
    # B is the second option text; map by option text
    answers = {}
    for q in mock["questions"]:
        answers[q["id"]] = next(o["id"] for o in q["options"] if o["text"] == "B")
    first = list(answers)[0]
    wrong = {first: next(o["id"] for o in mock["questions"][0]["options"] if o["text"] == "A")}
    assert (await api.put(f"{EP}/mocks/{mock['id']}/answers", json={"answers": wrong}, headers=stu.h)).status_code == 200
    assert (await api.get(f"{EP}/mocks/{mock['id']}", headers=w.students[1].h)).status_code == 404  # not yours
    done = await api.post(f"{EP}/mocks/{mock['id']}/submit", json={"answers": {k: v for k, v in answers.items() if k != first}}, headers=stu.h)
    assert done.status_code == 200, done.text
    d = done.json()
    assert d["status"] == "SUBMITTED" and d["score"] == 4.0 and d["max_score"] == 5.0 and d["percentage"] == 80.0
    assert sum(1 for x in d["review"] if x["correct"]) == 4 and all(x["correct_option_id"] for x in d["review"])
    assert (await api.post(f"{EP}/mocks/{mock['id']}/submit", json={"answers": answers}, headers=stu.h)).json()["score"] == 4.0  # idempotent
    # readiness + history
    rd = (await api.get(f"{EP}/readiness", headers=stu.h)).json()
    assert rd["has_data"] and rd["subjects"][0]["mock_count"] == 1 and rd["subjects"][0]["band"] == "ready"
    assert len((await api.get(f"{EP}/mocks", headers=stu.h)).json()) == 1
    # parent of student 0 sees it; the other parent and other school see nothing
    assert (await api.get(f"{EP}/children/{stu.profile_id}/readiness", headers=w.parent.h)).json()["subjects"][0]["mock_count"] == 1
    assert len((await api.get(f"{EP}/children/{stu.profile_id}/mocks", headers=w.parent.h)).json()) == 1
    assert (await api.get(f"{EP}/children/{stu.profile_id}/readiness", headers=w.parent_other.h)).status_code == 404
    assert (await api.get(f"{EP}/children/{stu.profile_id}/readiness", headers=w.other_admin.h)).status_code == 404
    # teachers see no rows at the database level
    from tests.integration.test_parent_messages_reading_rls import as_, count
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM exam_mock_attempts") == 0


async def test_late_submission_uses_only_saved_answers(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.commit()
    await _released_pool(api, w)
    stu = w.students[0]
    mock = (await api.post(f"{EP}/mocks", json={"subject_id": str(w.subject_id), "question_count": 5}, headers=stu.h)).json()
    q0 = mock["questions"][0]
    b = next(o["id"] for o in q0["options"] if o["text"] == "B")
    assert (await api.put(f"{EP}/mocks/{mock['id']}/answers", json={"answers": {q0["id"]: b}}, headers=stu.h)).status_code == 200
    await db_session.execute(text("UPDATE exam_mock_attempts SET deadline_at = clock_timestamp() - interval '5 minutes' WHERE id = :i"), {"i": mock["id"]})
    await db_session.commit()
    assert (await api.put(f"{EP}/mocks/{mock['id']}/answers", json={"answers": {}}, headers=stu.h)).status_code == 409
    all_b = {q["id"]: next(o["id"] for o in q["options"] if o["text"] == "B") for q in mock["questions"]}
    d = (await api.post(f"{EP}/mocks/{mock['id']}/submit", json={"answers": all_b}, headers=stu.h)).json()
    assert d["submit_reason"] == "TIMED_OUT" and d["score"] == 1.0  # only the answer saved before the deadline counts
