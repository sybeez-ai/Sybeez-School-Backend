"""Row-level security of the 0067 tables, exercised as the production role (sybeez_app)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.online_tests_world import build_world

pytestmark = pytest.mark.asyncio


async def as_(db, person, *, role=None, school=None):
    await db.execute(text("RESET ROLE"))
    await db.execute(text("SET ROLE sybeez_app"))
    for k, v in (("app.school_id", school or person.school_id), ("app.user_id", person.user_id),
                 ("app.role", role or person.role), ("app.rls_bypass", "off")):
        await db.execute(text("SELECT set_config(:k, :v, false)"), {"k": k, "v": str(v)})


async def owner(db):
    await db.execute(text("RESET ROLE"))


async def count(db, sql, **p):
    return (await db.execute(text(sql), p)).scalar_one()


async def denied(db, sql, **p):
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.begin_nested():
            await db.execute(text(sql), p)


@pytest.fixture
async def w(db_session):
    return await build_world(db_session)


async def _translation(db, w, *, published=True, class_id="cls", status="AUTO"):
    tid = uuid4()
    await db.execute(
        text("""INSERT INTO message_translations (id, school_id, source_type, source_id, class_id, is_published, language,
                title, body, status, needs_review, source_hash, created_at, updated_at)
                VALUES (:id, :s, 'announcement', :src, :c, :pub, 'ta', 't', 'b', :st, false, 'h', now(), now())"""),
        {"id": tid, "s": w.school_id, "src": uuid4(), "c": w.class_id if class_id == "cls" else None, "pub": published,
         "st": status},
    )
    return tid


async def test_parent_preferences_are_own_row_only(db_session, w):
    await owner(db_session)
    await db_session.execute(
        text("""INSERT INTO parent_message_preferences (id, school_id, user_id, preferred_language, whatsapp_opt_in,
                created_at, updated_at) VALUES (:i, :s, :u, 'hi', true, now(), now())"""),
        {"i": uuid4(), "s": w.school_id, "u": w.parent.user_id},
    )
    await as_(db_session, w.parent)
    assert await count(db_session, "SELECT count(*) FROM parent_message_preferences") == 1
    await as_(db_session, w.parent_other)
    assert await count(db_session, "SELECT count(*) FROM parent_message_preferences") == 0
    await denied(db_session, """INSERT INTO parent_message_preferences (id, school_id, user_id, preferred_language,
        whatsapp_opt_in, created_at, updated_at) VALUES (gen_random_uuid(), :s, :u, 'ta', true, now(), now())""",
                 s=w.school_id, u=w.parent.user_id)  # cannot consent on someone else's behalf
    await as_(db_session, w.admin)
    await denied(db_session, """INSERT INTO parent_message_preferences (id, school_id, user_id, preferred_language,
        whatsapp_opt_in, created_at, updated_at) VALUES (gen_random_uuid(), :s, :u, 'ta', true, now(), now())""",
                 s=w.school_id, u=w.parent_other.user_id)  # nor can the school
    await as_(db_session, w.students[0])
    assert await count(db_session, "SELECT count(*) FROM parent_message_preferences") == 0
    await as_(db_session, w.other_admin)
    assert await count(db_session, "SELECT count(*) FROM parent_message_preferences") == 0


async def test_translations_scope_by_publish_class_role_and_school(db_session, w):
    await owner(db_session)
    pub = await _translation(db_session, w)
    draft = await _translation(db_session, w, published=False)
    school_wide = await _translation(db_session, w, class_id=None)
    await as_(db_session, w.parent)  # linked to a child in the class
    seen = {r[0] for r in (await db_session.execute(text("SELECT id FROM message_translations"))).all()}
    assert seen == {pub, school_wide}
    await as_(db_session, w.parent_other)  # no child in the class: only school-wide
    seen = {r[0] for r in (await db_session.execute(text("SELECT id FROM message_translations"))).all()}
    assert seen == {school_wide}
    await as_(db_session, w.students[0])
    assert await count(db_session, "SELECT count(*) FROM message_translations") == 0
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM message_translations") == 3
    await as_(db_session, w.teacher2)  # other class
    seen = {r[0] for r in (await db_session.execute(text("SELECT id FROM message_translations"))).all()}
    assert seen == {school_wide}
    await as_(db_session, w.other_admin)
    assert await count(db_session, "SELECT count(*) FROM message_translations") == 0
    del draft


async def test_parent_may_cache_auto_translation_but_never_edit(db_session, w):
    await as_(db_session, w.parent)
    ins = """INSERT INTO message_translations (id, school_id, source_type, source_id, class_id, is_published, language,
             title, body, status, needs_review, edited_by, source_hash, created_at, updated_at)
             VALUES (gen_random_uuid(), :s, 'announcement', gen_random_uuid(), :c, true, 'hi', 't', 'b', :st, false, :eb, 'h', now(), now())"""
    await db_session.execute(text(ins), {"s": w.school_id, "c": w.class_id, "st": "AUTO", "eb": None})
    await denied(db_session, ins, s=w.school_id, c=w.class_id, st="EDITED", eb=w.parent.user_id)
    await denied(db_session, ins, s=w.other_school_id, c=w.class_id, st="AUTO", eb=None)  # wrong school
    await owner(db_session)
    tid = await _translation(db_session, w)
    await as_(db_session, w.parent)
    updated = (await db_session.execute(text("UPDATE message_translations SET title='x' WHERE id=:i RETURNING id"), {"i": tid})).all()
    assert updated == []  # RLS filters the row: a parent cannot change a translation


async def test_progress_updates_only_reach_the_students_parents(db_session, w):
    await owner(db_session)
    pid = uuid4()
    await db_session.execute(
        text("""INSERT INTO student_progress_updates (id, school_id, student_id, kind, title, body, is_published,
                created_at, updated_at) VALUES (:i, :s, :st, 'PROGRESS', 't', 'b', true, now(), now())"""),
        {"i": pid, "s": w.school_id, "st": w.students[0].profile_id},
    )
    await as_(db_session, w.parent)
    assert await count(db_session, "SELECT count(*) FROM student_progress_updates") == 1
    for who in (w.parent_other, w.students[0], w.teacher2, w.other_admin):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM student_progress_updates") == 0, who.role
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM student_progress_updates") == 1
    await as_(db_session, w.parent)
    await denied(db_session, """INSERT INTO student_progress_updates (id, school_id, student_id, kind, title, body,
        is_published, created_at, updated_at) VALUES (gen_random_uuid(), :s, :st, 'PROGRESS', 't', 'b', true, now(), now())""",
                 s=w.school_id, st=w.students[0].profile_id)


async def test_reading_passages_library_and_school_scope(db_session, w):
    await owner(db_session)
    other = uuid4()
    await db_session.execute(
        text("""INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source, is_active,
                created_at, updated_at) VALUES (:i, :s, 2, 'Other school', 'x y z', 3, 'SCHOOL', true, now(), now())"""),
        {"i": other, "s": w.other_school_id},
    )
    await as_(db_session, w.students[0])
    assert await count(db_session, "SELECT count(*) FROM reading_passages WHERE school_id IS NULL") >= 15
    assert await count(db_session, "SELECT count(*) FROM reading_passages WHERE id = :i", i=other) == 0
    await denied(db_session, """INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source,
        is_active, created_at, updated_at) VALUES (gen_random_uuid(), :s, 1, 'mine', 'a b c', 3, 'SCHOOL', true, now(), now())""",
                 s=w.school_id)  # students cannot add passages
    await as_(db_session, w.teacher)
    await db_session.execute(
        text("""INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source, is_active,
                created_at, updated_at) VALUES (gen_random_uuid(), :s, 1, 'mine', 'a b c', 3, 'SCHOOL', true, now(), now())"""),
        {"s": w.school_id},
    )
    await denied(db_session, """INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source,
        is_active, created_at, updated_at) VALUES (gen_random_uuid(), NULL, 1, 'lib', 'a b c', 3, 'LIBRARY', true, now(), now())""")
    await denied(db_session, """INSERT INTO reading_passages (id, school_id, class_level, title, body, word_count, source,
        is_active, created_at, updated_at) VALUES (gen_random_uuid(), :s, 1, 'x', 'a b c', 3, 'SCHOOL', true, now(), now())""",
                 s=w.other_school_id)
    await as_(db_session, w.other_teacher)
    assert await count(db_session, "SELECT count(*) FROM reading_passages WHERE title = 'mine'") == 0


async def test_reading_sessions_are_private_to_student_teacher_and_linked_parent(db_session, w):
    await owner(db_session)
    passage = (await db_session.execute(text("SELECT id FROM reading_passages WHERE school_id IS NULL LIMIT 1"))).scalar_one()
    sid = uuid4()
    await db_session.execute(
        text("""INSERT INTO reading_sessions (id, school_id, student_id, passage_id, status, started_at, words_total,
                words_correct, words_misread, words_skipped, stars, transcript, retention_until, created_at, updated_at)
                VALUES (:i, :s, :st, :p, 'COMPLETED', now(), 10, 9, 1, 0, 2, 'secret words', current_date + 90, now(), now())"""),
        {"i": sid, "s": w.school_id, "st": w.students[0].profile_id, "p": passage},
    )
    for who in (w.students[0], w.parent, w.teacher, w.admin):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM reading_sessions") == 1, who.role
    for who in (w.students[1], w.parent_other, w.teacher2, w.other_admin, w.other_teacher):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM reading_sessions") == 0, who.role
    # parents/teachers read only; nobody but the retention sweeper deletes
    for who in (w.parent, w.teacher):
        await as_(db_session, who)
        assert (await db_session.execute(text("UPDATE reading_sessions SET stars=3 RETURNING id"))).all() == []
    await as_(db_session, w.students[0])
    assert (await db_session.execute(text("DELETE FROM reading_sessions RETURNING id"))).all() == []
    await denied(db_session, """INSERT INTO reading_sessions (id, school_id, student_id, passage_id, status, started_at,
        retention_until, created_at, updated_at) VALUES (gen_random_uuid(), :s, :st, :p, 'STARTED', now(), current_date, now(), now())""",
                 s=w.school_id, st=w.students[1].profile_id, p=passage)  # cannot write for a classmate
    await as_(db_session, w.students[0])
    await db_session.execute(
        text("""UPDATE reading_sessions SET transcript = NULL WHERE id = :i"""), {"i": sid})  # own retention purge works
