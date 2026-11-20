"""Row-level security for the online-test tables, exercised directly as the production role.

Everything runs in the rolled-back test transaction: seed as the table owner, then `SET ROLE sybeez_app`
with the same GUCs the API sets, and read/write raw SQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integration.online_tests_world import Person, World, build_world

pytestmark = pytest.mark.asyncio


async def as_(db, person: Person | None, *, role: str | None = None, bypass: bool = False, school=None):
    await db.execute(text("RESET ROLE"))
    await db.execute(text("SET ROLE sybeez_app"))
    sid = school or (person.school_id if person else "")
    uid = person.user_id if person else ""
    r = role or (person.role if person else "")
    for k, v in (("app.school_id", sid), ("app.user_id", uid), ("app.role", r), ("app.rls_bypass", "on" if bypass else "off")):
        await db.execute(text("SELECT set_config(:k, :v, false)"), {"k": k, "v": str(v)})


async def owner(db):
    await db.execute(text("RESET ROLE"))


@pytest.fixture
async def seeded(db_session):
    w = await build_world(db_session)
    now = datetime.now(timezone.utc)
    tid, draft_id, qid = uuid4(), uuid4(), uuid4()
    opt = [{"id": "a1", "text": "A"}, {"id": "b2", "text": "B"}]
    s0 = w.students[0]
    attempt_id = uuid4()
    other_attempt = uuid4()
    for t_id, status in ((tid, "PUBLISHED"), (draft_id, "DRAFT")):
        await db_session.execute(
            text("""INSERT INTO online_tests (id, school_id, teacher_id, created_by, class_id, subject_id, title, status,
                    duration_minutes, total_points, settings, created_at, updated_at)
                    VALUES (:id, :s, :t, :u, :c, :sub, 'T', :st, 10, 1, '{}', now(), now())"""),
            {"id": t_id, "s": w.school_id, "t": w.teacher.profile_id, "u": w.teacher.user_id, "c": w.class_id, "sub": w.subject_id, "st": status},
        )
    await db_session.execute(
        text("""INSERT INTO online_test_questions (id, school_id, test_id, class_id, prompt, options, points, sort_order, source, ai_reviewed, created_at, updated_at)
                VALUES (:id, :s, :t, :c, 'Q', CAST(:o AS jsonb), 1, 0, 'MANUAL', true, now(), now())"""),
        {"id": qid, "s": w.school_id, "t": tid, "c": w.class_id, "o": '[{"id":"a1","text":"A"},{"id":"b2","text":"B"}]'},
    )
    await db_session.execute(
        text("""INSERT INTO online_test_question_keys (question_id, school_id, test_id, class_id, correct_option_id, created_at, updated_at)
                VALUES (:q, :s, :t, :c, 'a1', now(), now())"""),
        {"q": qid, "s": w.school_id, "t": tid, "c": w.class_id},
    )
    for aid, student in ((attempt_id, s0), (other_attempt, w.students[1])):
        await db_session.execute(
            text("""INSERT INTO online_test_attempts (id, school_id, test_id, student_id, class_id, status, started_at, deadline_at,
                    question_order, option_order, violation_count, resume_count, review_status, created_at, updated_at)
                    VALUES (:id, :s, :t, :st, :c, 'IN_PROGRESS', now(), now() + interval '10 minutes', '[]', '{}', 0, 0, 'NONE', now(), now())"""),
            {"id": aid, "s": w.school_id, "t": tid, "st": student.profile_id, "c": w.class_id},
        )
    del now, opt
    return w, tid, draft_id, qid, attempt_id, other_attempt


async def count(db, sql: str, **params) -> int:
    return (await db.execute(text(sql), params)).scalar_one()


async def denied(db, sql: str, **params) -> None:
    """The statement must be rejected by RLS (inside a savepoint so the test transaction survives)."""
    with pytest.raises(DBAPIError, match="row-level security"):
        async with db.begin_nested():
            await db.execute(text(sql), params)


async def affected(db, sql: str, **params) -> int:
    """Rows changed by an UPDATE/DELETE. RLS either filters the rows out (0) or rejects the write (also 0)."""
    try:
        async with db.begin_nested():
            return (await db.execute(text(sql), params)).rowcount
    except DBAPIError as exc:
        assert "row-level security" in str(exc)
        return 0


async def test_answer_keys_only_for_staff_and_the_classes_teacher(db_session, seeded):
    w, tid, _d, _q, _a, _o = seeded
    q = "SELECT count(*) FROM online_test_question_keys WHERE test_id = :t"
    for who, expected in (
        (w.students[0], 0), (w.parent, 0), (w.teacher2, 0), (w.other_teacher, 0), (w.other_admin, 0),
        (w.teacher, 1), (w.admin, 1),
    ):
        await as_(db_session, who)
        assert await count(db_session, q, t=tid) == expected, who.role
    await as_(db_session, None)
    assert await count(db_session, q, t=tid) == 0  # no tenant context fails closed


async def test_students_cannot_write_keys_or_questions(db_session, seeded):
    w, tid, _d, qid, _a, _o = seeded
    await as_(db_session, w.students[0])
    assert await affected(db_session, "UPDATE online_test_question_keys SET correct_option_id = 'b2' WHERE test_id = :t", t=tid) == 0
    assert await affected(db_session, "DELETE FROM online_test_question_keys WHERE test_id = :t", t=tid) == 0
    await denied(
        db_session,
        """INSERT INTO online_test_question_keys (question_id, school_id, test_id, class_id, correct_option_id, created_at, updated_at)
           VALUES (:q, :s, :t, :c, 'b2', now(), now())""",
        q=uuid4(), s=w.school_id, t=tid, c=w.class_id,
    )
    assert await affected(db_session, "UPDATE online_test_questions SET prompt = 'hacked' WHERE id = :q", q=qid) == 0
    await as_(db_session, w.teacher2)
    assert await affected(db_session, "UPDATE online_test_question_keys SET correct_option_id = 'b2' WHERE test_id = :t", t=tid) == 0
    await owner(db_session)
    assert await count(db_session, "SELECT count(*) FROM online_test_question_keys WHERE correct_option_id = 'a1'") == 1


async def test_student_sees_questions_only_after_attempt_and_never_drafts(db_session, seeded):
    w, tid, draft_id, _q, _a, _o = seeded
    await as_(db_session, w.students[0])
    assert await count(db_session, "SELECT count(*) FROM online_test_questions WHERE test_id = :t", t=tid) == 1
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=draft_id) == 0
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=tid) == 1
    await as_(db_session, w.students[2])  # same class, no attempt yet
    assert await count(db_session, "SELECT count(*) FROM online_test_questions WHERE test_id = :t", t=tid) == 0
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=tid) == 1
    await as_(db_session, w.student_other_class)
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=tid) == 0
    await as_(db_session, w.teacher2)
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=tid) == 0
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=draft_id) == 1
    await as_(db_session, w.admin)
    assert await count(db_session, "SELECT count(*) FROM online_tests WHERE id = :t", t=draft_id) == 1
    await as_(db_session, w.other_admin)
    assert await count(db_session, "SELECT count(*) FROM online_tests") == 0
    await as_(db_session, w.students[0])
    await denied(
        db_session,
        """INSERT INTO online_tests (id, school_id, class_id, subject_id, title, status, duration_minutes, total_points, settings, created_at, updated_at)
           VALUES (:i, :s, :c, :sub, 'x', 'PUBLISHED', 5, 0, '{}', now(), now())""",
        i=uuid4(), s=w.school_id, c=w.class_id, sub=w.subject_id,
    )


async def test_attempt_visibility_and_no_direct_student_writes(db_session, seeded):
    w, tid, _d, _q, attempt_id, _o = seeded
    q = "SELECT count(*) FROM online_test_attempts WHERE test_id = :t"
    for who, expected in (
        (w.students[0], 1), (w.students[1], 1), (w.students[2], 0), (w.parent, 1), (w.parent_other, 0),
        (w.teacher, 2), (w.teacher2, 0), (w.admin, 2), (w.other_admin, 0), (w.other_teacher, 0),
    ):
        await as_(db_session, who)
        assert await count(db_session, q, t=tid) == expected, (who.role, expected)
    await as_(db_session, w.students[0])
    assert await affected(db_session, "UPDATE online_test_attempts SET score = 99 WHERE id = :i", i=attempt_id) == 0
    await as_(db_session, w.parent)
    assert await affected(db_session, "UPDATE online_test_attempts SET score = 99 WHERE id = :i", i=attempt_id) == 0
    await denied(
        db_session,
        """INSERT INTO online_test_attempts (id, school_id, test_id, student_id, class_id, status, started_at, deadline_at,
           question_order, option_order, violation_count, resume_count, review_status, created_at, updated_at)
           VALUES (:id, :s, :t, :st, :c, 'IN_PROGRESS', now(), now() + interval '1 day', '[]', '{}', 0, 0, 'NONE', now(), now())""",
        id=uuid4(), s=w.school_id, t=tid, st=w.students[2].profile_id, c=w.class_id,
    )
    await as_(db_session, w.teacher)  # teachers review (REOPEN/VOID), so they may update
    assert await affected(db_session, "UPDATE online_test_attempts SET review_note = 'ok' WHERE id = :i", i=attempt_id) == 1
    await as_(db_session, w.teacher2)
    assert await affected(db_session, "UPDATE online_test_attempts SET review_note = 'no' WHERE id = :i", i=attempt_id) == 0
    await owner(db_session)
    assert await count(db_session, "SELECT count(*) FROM online_test_attempts WHERE score IS NOT NULL") == 0


EVENT_SQL = """INSERT INTO online_test_events (id, school_id, attempt_id, test_id, student_id, class_id, type, counted, server_at)
               VALUES (:id, :s, :a, :t, :st, :c, 'TAB_HIDDEN', true, clock_timestamp())"""


def _ev(w, tid, attempt_id, student):
    return dict(id=uuid4(), s=w.school_id, a=attempt_id, t=tid, st=student.profile_id, c=w.class_id)


async def test_events_append_only_and_only_while_the_attempt_is_open(db_session, seeded):
    w, tid, _d, _q, attempt_id, other_attempt = seeded
    s0, s1 = w.students[0], w.students[1]
    await as_(db_session, s0)
    await db_session.execute(text(EVENT_SQL), _ev(w, tid, attempt_id, s0))  # own, open attempt
    await denied(db_session, EVENT_SQL, **_ev(w, tid, other_attempt, s1))  # someone else's attempt
    assert await count(db_session, "SELECT count(*) FROM online_test_events") == 0  # students cannot read the log
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM online_test_events WHERE attempt_id = :a", a=attempt_id) == 1
    await as_(db_session, w.teacher2)
    assert await count(db_session, "SELECT count(*) FROM online_test_events") == 0
    await as_(db_session, w.parent)
    assert await count(db_session, "SELECT count(*) FROM online_test_events") == 0
    # nobody can rewrite or erase the log through the API role, not even a school admin
    for who in (s0, w.teacher, w.admin):
        await as_(db_session, who)
        assert await affected(db_session, "UPDATE online_test_events SET counted = false WHERE attempt_id = :a", a=attempt_id) == 0
        assert await affected(db_session, "DELETE FROM online_test_events WHERE attempt_id = :a", a=attempt_id) == 0
    await owner(db_session)
    assert await count(db_session, "SELECT count(*) FROM online_test_events WHERE counted") == 1
    # once the attempt is submitted (or past its deadline) a student can no longer add events
    await db_session.execute(text("UPDATE online_test_attempts SET status = 'SUBMITTED' WHERE id = :a"), {"a": attempt_id})
    await as_(db_session, s0)
    await denied(db_session, EVENT_SQL, **_ev(w, tid, attempt_id, s0))
    await owner(db_session)
    await db_session.execute(text("UPDATE online_test_attempts SET status = 'IN_PROGRESS', deadline_at = clock_timestamp() - interval '1 minute' WHERE id = :a"), {"a": attempt_id})
    await as_(db_session, s0)
    await denied(db_session, EVENT_SQL, **_ev(w, tid, attempt_id, s0))


ANSWER_SQL = """INSERT INTO online_test_answers (id, school_id, attempt_id, test_id, student_id, class_id, question_id, selected_option_id, flagged, answered_at)
                VALUES (:id, :s, :a, :t, :st, :c, :q, 'a1', false, now())"""


async def test_answers_only_by_the_owner_while_open_and_never_parents(db_session, seeded):
    w, tid, _d, qid, attempt_id, other_attempt = seeded
    s0, s1 = w.students[0], w.students[1]
    params = dict(id=uuid4(), s=w.school_id, a=attempt_id, t=tid, st=s0.profile_id, c=w.class_id, q=qid)
    await as_(db_session, s0)
    await db_session.execute(text(ANSWER_SQL), params)
    await denied(db_session, ANSWER_SQL, **{**params, "id": uuid4(), "a": other_attempt, "st": s1.profile_id})
    assert await count(db_session, "SELECT count(*) FROM online_test_answers") == 1
    await as_(db_session, s1)
    assert await count(db_session, "SELECT count(*) FROM online_test_answers") == 0  # classmates never see each other's answers
    await as_(db_session, w.parent)
    assert await count(db_session, "SELECT count(*) FROM online_test_answers") == 0
    await as_(db_session, w.teacher)
    assert await count(db_session, "SELECT count(*) FROM online_test_answers") == 1
    await owner(db_session)
    await db_session.execute(text("UPDATE online_test_attempts SET status = 'AUTO_SUBMITTED' WHERE id = :a"), {"a": attempt_id})
    await as_(db_session, s0)
    assert await affected(db_session, "UPDATE online_test_answers SET selected_option_id = 'b2' WHERE attempt_id = :a", a=attempt_id) == 0
    await denied(db_session, ANSWER_SQL, **{**params, "id": uuid4(), "question_id": None})


async def test_school_settings_and_ai_usage_and_audit(db_session, seeded):
    w, tid, _d, _q, _a, _o = seeded
    await owner(db_session)
    await db_session.execute(text("INSERT INTO online_test_school_settings (school_id, enabled, ai_monthly_cap, default_max_violations, updated_at) VALUES (:s, true, 50, 3, now())"), {"s": w.school_id})
    await db_session.execute(text("INSERT INTO online_test_ai_usage (id, school_id, teacher_user_id, test_id, period, requested, returned, created_at) VALUES (:i, :s, :u, :t, '2026-10', 5, 5, now())"),
                             {"i": uuid4(), "s": w.school_id, "u": w.teacher.user_id, "t": tid})
    for who, expected in ((w.students[0], 1), (w.parent, 1), (w.teacher, 1), (w.admin, 1), (w.other_admin, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM online_test_school_settings") == expected, who.role
    await as_(db_session, w.students[0])
    assert await affected(db_session, "UPDATE online_test_school_settings SET enabled = false", ) == 0
    await as_(db_session, w.teacher)
    assert await affected(db_session, "UPDATE online_test_school_settings SET ai_monthly_cap = 99999") == 0
    await as_(db_session, w.admin)
    assert await affected(db_session, "UPDATE online_test_school_settings SET ai_monthly_cap = 10") == 1
    await as_(db_session, w.other_admin)
    assert await affected(db_session, "UPDATE online_test_school_settings SET ai_monthly_cap = 1") == 0
    # AI usage: students and parents see nothing; teachers and admins do
    for who, expected in ((w.students[0], 0), (w.parent, 0), (w.teacher, 1), (w.admin, 1), (w.other_admin, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM online_test_ai_usage") == expected, who.role
    # audit: insert-only; teachers read only their own actions; students nothing
    audit = "INSERT INTO online_test_audit_logs (id, school_id, test_id, actor_user_id, action, created_at) VALUES (:i, :s, :t, :u, 'X', now())"
    await as_(db_session, w.teacher)
    await db_session.execute(text(audit), dict(i=uuid4(), s=w.school_id, t=tid, u=w.teacher.user_id))
    await as_(db_session, w.admin)
    await db_session.execute(text(audit), dict(i=uuid4(), s=w.school_id, t=tid, u=w.admin.user_id))
    for who, expected in ((w.teacher, 1), (w.admin, 2), (w.students[0], 0), (w.parent, 0), (w.teacher2, 0), (w.other_admin, 0)):
        await as_(db_session, who)
        assert await count(db_session, "SELECT count(*) FROM online_test_audit_logs") == expected, who.role
    for who in (w.teacher, w.admin):
        await as_(db_session, who)
        assert await affected(db_session, "UPDATE online_test_audit_logs SET action = 'Y'") == 0
        assert await affected(db_session, "DELETE FROM online_test_audit_logs") == 0
    await as_(db_session, w.students[0])
    await denied(db_session, audit, i=uuid4(), s=w.school_id, t=tid, u=w.students[0].user_id)
    await as_(db_session, w.other_admin)
    await denied(db_session, audit, i=uuid4(), s=w.school_id, t=tid, u=w.other_admin.user_id)
