"""RLS of ai_grade_suggestions (0071)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_parent_messages_reading_rls import as_, count, owner

pytestmark = pytest.mark.asyncio


async def test_only_staff_and_class_teachers_see_suggestions(db_session):
    w = await build_world(db_session)
    await owner(db_session)
    hid, sid = uuid4(), uuid4()
    await db_session.execute(
        text("""INSERT INTO homework (id, school_id, class_id, subject_id, teacher_id, title, status, max_marks, homework_type,
                allow_resubmission, created_at, updated_at) VALUES (:i, :s, :c, :sub, :t, 'E', 'PUBLISHED', 10, 'ASSIGNMENT', true, now(), now())"""),
        {"i": hid, "s": w.school_id, "c": w.class_id, "sub": w.subject_id, "t": w.teacher.profile_id},
    )
    await db_session.execute(
        text("""INSERT INTO homework_submissions (id, homework_id, student_id, school_id, submitted_at, content, status,
                created_at, updated_at) VALUES (:i, :h, :st, :s, now(), 'a', 'SUBMITTED', now(), now())"""),
        {"i": sid, "h": hid, "st": w.students[0].profile_id, "s": w.school_id},
    )
    await db_session.execute(
        text("""INSERT INTO ai_grade_suggestions (id, school_id, submission_id, class_id, suggested_marks, max_marks, reasoning,
                strengths, improve, confidence, status, created_at, updated_at)
                VALUES (gen_random_uuid(), :s, :sub, :c, 5, 10, 'r', '[]', '[]', 'low', 'SUGGESTED', now(), now())"""),
        {"s": w.school_id, "sub": sid, "c": w.class_id},
    )
    for who, expected in ((w.admin, 1), (w.teacher, 1), (w.teacher2, 0), (w.students[0], 0), (w.parent, 0),
                          (w.other_admin, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM ai_grade_suggestions") == expected
