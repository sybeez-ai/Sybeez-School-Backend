from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_heatmap_scoped_and_computed(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    for i, v in ((0, 40.0), (1, 80.0)):
        await db_session.execute(
            text("""INSERT INTO student_progress (id, school_id, student_id, subject_id, metric, value, created_at, updated_at)
                    VALUES (:i, :s, :st, :sub, 'overall', :v, now(), now())"""),
            {"i": uuid4(), "s": w.school_id, "st": w.students[i].profile_id, "sub": w.subject_id, "v": v},
        )
    await db_session.commit()
    url = f"/api/v1/analytics/classes/{w.class_id}/heatmap"
    r = await api.get(url, headers=w.admin.h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(j["subjects"]) == 1 and j["subjects"][0]["average"] == 60.0
    bands = sorted(c["band"] for st in j["students"] for c in st["cells"])
    assert bands == ["needs-work", "no-data", "strong"]
    assert any(st["average"] is None for st in j["students"])  # third student has no marks
    assert (await api.get(url, headers=w.teacher.h)).status_code == 200
    assert (await api.get(url, headers=w.teacher2.h)).status_code == 403
    for p in (w.students[0], w.parent):
        assert (await api.get(url, headers=p.h)).status_code == 403
    assert (await api.get(url, headers=w.other_admin.h)).json()["students"] == []
