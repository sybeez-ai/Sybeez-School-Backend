from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_student_sees_only_own_path(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    await db_session.execute(
        text("""INSERT INTO practice_attempts (id, school_id, student_user_id, subject_id, topic, difficulty, questions_json,
                answers_json, score_pct, completed_at, created_at) VALUES (:i, :s, :u, :sub, 'Fractions', 'adaptive', '[]', '[]', 40, now(), now())"""),
        {"i": uuid4(), "s": w.school_id, "u": w.students[0].user_id, "sub": w.subject_id},
    )
    await db_session.commit()
    r = await api.get("/api/v1/learning-paths/me", headers=w.students[0].h)
    assert r.status_code == 200, r.text
    assert r.json()["topics"][0]["status"] == "weak"
    assert (await api.get("/api/v1/learning-paths/me", headers=w.students[1].h)).json()["has_data"] is False
    assert (await api.get("/api/v1/learning-paths/me", headers=w.teacher.h)).status_code == 403
