"""Online MCQ tests: tables, grants and explicit forced row-level security.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-16

0042 classified the tables that existed on the day it ran. These tables are new, so each
gets an explicit policy here. Highlights:

* online_test_question_keys is readable only by school admins, the service role and the
  teachers of the test's class. Students and parents never see a row.
* Students may read a test's questions only once they have an attempt on it, and may write
  answers / violation events only while their attempt is IN_PROGRESS and before its deadline.
* online_test_events is append-only for everyone except the RLS-bypass sweeper.

Fresh databases create these tables early from 0001's metadata.create_all, and 0042's generic
loop may already have attached `<table>_school` / `<table>_role` policies to them. Those are
dropped first so the explicit policies below are the only ones in force.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0064"
down_revision: Union[str, None] = "0063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "online_tests",
    "online_test_questions",
    "online_test_question_keys",
    "online_test_attempts",
    "online_test_answers",
    "online_test_events",
    "online_test_school_settings",
    "online_test_ai_usage",
    "online_test_audit_logs",
)

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"
STAFF = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')"
STAFF_OR_TEACHER = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'TEACHER')"
CLASS_OK = "app.can_see('class_content', NULL::uuid, class_id, NULL::uuid)"
STUDENT_OK = "app.can_see('student_data', student_id, class_id, NULL::uuid)"


def _policy(table: str, name: str, command: str, using: str | None, check: str | None) -> None:
    parts = [f"CREATE POLICY {name} ON {table} FOR {command}"]
    if using:
        parts.append(f"USING ({using})")
    if check:
        parts.append(f"WITH CHECK ({check})")
    op.execute(" ".join(parts))


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import online_test as models

    del models
    Base.metadata.create_all(
        op.get_bind(),
        tables=[Base.metadata.tables[name] for name in TABLES],
        checkfirst=True,
    )

    # SECURITY DEFINER helpers (owned by the BYPASSRLS role so they can look across tables).
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.ot_student_has_attempt(tid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT EXISTS (
            SELECT 1
            FROM online_test_attempts a
            JOIN students s ON s.id = a.student_id
            WHERE a.test_id = tid
              AND s.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
              AND s.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
          )
        $$
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.ot_attempt_open(aid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT EXISTS (
            SELECT 1
            FROM online_test_attempts a
            WHERE a.id = aid
              AND a.status = 'IN_PROGRESS'
              AND clock_timestamp() <= a.deadline_at + interval '5 seconds'
          )
        $$
        """
    )
    for fn in ("app.ot_student_has_attempt(uuid)", "app.ot_attempt_open(uuid)"):
        op.execute(f"ALTER FUNCTION {fn} OWNER TO sybeez_definer")
        op.execute(f"REVOKE ALL ON FUNCTION {fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {fn} TO sybeez_app")

    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
        op.execute(f"GRANT SELECT ON {table} TO sybeez_definer")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        for old in ("school", "role", "tenant", "access", "select", "insert", "update", "delete", "bypass"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{old} ON {table}")

    # online_tests: drafts are visible to staff and teachers only.
    _policy(
        "online_tests",
        "online_tests_access",
        "ALL",
        f"{BYPASS} OR ({SCHOOL} AND {CLASS_OK} AND (status <> 'DRAFT' OR {STAFF_OR_TEACHER}))",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND {CLASS_OK})",
    )

    # questions: staff, the class's teachers, and a student who already has an attempt.
    _policy(
        "online_test_questions",
        "online_test_questions_access",
        "ALL",
        f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND {CLASS_OK}) "
        f"OR ({ROLE} = 'STUDENT' AND {CLASS_OK} AND app.ot_student_has_attempt(test_id))))",
        f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND {CLASS_OK})))",
    )

    # answer keys: never students or parents.
    keys_expr = f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND {CLASS_OK})))"
    _policy("online_test_question_keys", "online_test_question_keys_access", "ALL", keys_expr, keys_expr)

    # attempts: readable by the owner student, linked parents (score only, enforced by the API), the class
    # teachers and staff. Written only by staff/teachers (review) or by the API's service scope (start, grade).
    _policy(
        "online_test_attempts",
        "online_test_attempts_access",
        "ALL",
        f"{BYPASS} OR ({SCHOOL} AND {STUDENT_OK})",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND {STUDENT_OK})",
    )

    # answers: never parents; students only while the attempt is open.
    _policy(
        "online_test_answers",
        "online_test_answers_access",
        "ALL",
        f"{BYPASS} OR ({SCHOOL} AND {ROLE} <> 'PARENT' AND {STUDENT_OK})",
        f"{BYPASS} OR ({SCHOOL} AND {ROLE} <> 'PARENT' AND {STUDENT_OK} "
        f"AND ({ROLE} <> 'STUDENT' OR app.ot_attempt_open(attempt_id)))",
    )

    # events: append-only. Staff/teachers read; students insert while the attempt is open.
    _policy(
        "online_test_events",
        "online_test_events_select",
        "SELECT",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND {STUDENT_OK})",
        None,
    )
    _policy(
        "online_test_events",
        "online_test_events_insert",
        "INSERT",
        None,
        f"{BYPASS} OR ({SCHOOL} AND {ROLE} <> 'PARENT' AND {STUDENT_OK} "
        f"AND ({ROLE} <> 'STUDENT' OR app.ot_attempt_open(attempt_id)))",
    )
    _policy("online_test_events", "online_test_events_update", "UPDATE", BYPASS, BYPASS)
    _policy("online_test_events", "online_test_events_delete", "DELETE", BYPASS, None)

    # school settings: every school role can read the switch; only staff change it.
    _policy(
        "online_test_school_settings",
        "online_test_school_settings_access",
        "ALL",
        f"{BYPASS} OR {SCHOOL}",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF})",
    )

    # AI usage: teachers and staff record and read the monthly counter.
    _policy(
        "online_test_ai_usage",
        "online_test_ai_usage_access",
        "ALL",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER})",
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER})",
    )

    # audit log: insert-only for teachers (own actions) and staff; teachers read their own rows.
    _policy(
        "online_test_audit_logs",
        "online_test_audit_logs_select",
        "SELECT",
        f"{BYPASS} OR ({SCHOOL} AND ({STAFF} OR ({ROLE} = 'TEACHER' AND actor_user_id = {UID})))",
        None,
    )
    _policy(
        "online_test_audit_logs",
        "online_test_audit_logs_insert",
        "INSERT",
        None,
        f"{BYPASS} OR ({SCHOOL} AND {STAFF_OR_TEACHER} AND ({STAFF} OR actor_user_id = {UID}))",
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS app.ot_attempt_open(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app.ot_student_has_attempt(uuid)")
