"""Row-level security of substitute_assignments (0070), exercised as the production role."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.integration.online_tests_world import build_world
from tests.integration.test_parent_messages_reading_rls import as_, count, denied, owner

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def w(db_session):
    return await build_world(db_session)


async def _cover(db, w):
    await owner(db)
    entry = uuid4()
    await db.execute(
        text("""INSERT INTO timetable_entries (id, school_id, class_id, subject_id, teacher_id, day_of_week,
                starts_at, ends_at, created_at, updated_at)
                VALUES (:i, :s, :c, :sub, :t, 0, '09:00', '09:45', now(), now())"""),
        {"i": entry, "s": w.school_id, "c": w.class_id, "sub": w.subject_id, "t": w.teacher.profile_id},
    )
    await db.execute(
        text("""INSERT INTO substitute_assignments (id, school_id, timetable_entry_id, cover_date, absent_teacher_id,
                substitute_teacher_id, status, created_at, updated_at)
                VALUES (gen_random_uuid(), :s, :e, current_date, :a, :b, 'CONFIRMED', now(), now())"""),
        {"s": w.school_id, "e": entry, "a": w.teacher.profile_id, "b": w.teacher2.profile_id},
    )
    return entry


async def test_visibility(db_session, w):
    await _cover(db_session, w)
    for who, expected in ((w.admin, 1), (w.teacher, 1), (w.teacher2, 1), (w.students[0], 0), (w.parent, 0),
                          (w.other_admin, 0), (w.other_teacher, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM substitute_assignments") == expected


async def test_only_admin_writes(db_session, w):
    entry = await _cover(db_session, w)
    sql = """INSERT INTO substitute_assignments (id, school_id, timetable_entry_id, cover_date, absent_teacher_id,
             substitute_teacher_id, status, created_at, updated_at)
             VALUES (gen_random_uuid(), :s, :e, current_date + 1, :a, :b, 'CONFIRMED', now(), now())"""
    params = dict(s=w.school_id, e=entry, a=w.teacher.profile_id, b=w.teacher2.profile_id)
    await as_(db_session, w.teacher)
    await denied(db_session, sql, **params)
    await as_(db_session, w.other_admin)
    await denied(db_session, sql, **params)
    await as_(db_session, w.admin)
    await db_session.execute(text(sql), params)
