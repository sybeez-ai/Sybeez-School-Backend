"""Postgres RLS for every tenant table, plus parent/student/teacher scope.

Revision ID: 9002
Revises: 9001
Create Date: 2026-10-11

The application sets app.school_id, app.user_id, app.role, and app.membership_id
from the access token, then SET LOCAL ROLE sybeez_app. Superusers bypass RLS, so
authenticated requests must run as sybeez_app (NOBYPASSRLS).

Layer 1: school_id must match the token (platform admins may span schools).
Layer 2: parents see their children, students see themselves, teachers see their classes.

Range 9000-9099 is reserved for this platform branch.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "9002"
down_revision: Union[str, None] = "9001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTIONS = r"""
CREATE OR REPLACE FUNCTION rls_role() RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT COALESCE(current_setting('app.role', true), '')
$$;

CREATE OR REPLACE FUNCTION rls_uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION rls_school() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.school_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION rls_membership() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.membership_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION rls_tenant(row_school uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT rls_role() = 'PLATFORM_ADMIN'
     OR (rls_school() IS NOT NULL AND row_school IS NOT NULL AND row_school = rls_school())
$$;

CREATE OR REPLACE FUNCTION rls_privileged() RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT rls_role() IN ('PLATFORM_ADMIN', 'SCHOOL_ADMIN')
$$;

CREATE OR REPLACE FUNCTION rls_teacher_class(cid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT cid IS NOT NULL AND EXISTS (
    SELECT 1 FROM teacher_assignments ta
    JOIN teachers t ON t.id = ta.teacher_id
    WHERE ta.class_id = cid AND ta.school_id = rls_school() AND t.user_id = rls_uid()
  )
$$;

CREATE OR REPLACE FUNCTION rls_student_self(sid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT sid IS NOT NULL AND EXISTS (
    SELECT 1 FROM students s
    WHERE s.id = sid AND s.user_id = rls_uid() AND s.school_id = rls_school()
  )
$$;

CREATE OR REPLACE FUNCTION rls_parent_child(sid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT sid IS NOT NULL AND EXISTS (
    SELECT 1 FROM student_parents sp
    JOIN parents p ON p.id = sp.parent_id
    WHERE sp.student_id = sid AND p.user_id = rls_uid() AND sp.school_id = rls_school()
  )
$$;

CREATE OR REPLACE FUNCTION rls_teacher_student(sid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT sid IS NOT NULL AND EXISTS (
    SELECT 1 FROM enrollments e
    JOIN teacher_assignments ta ON ta.class_id = e.class_id AND ta.school_id = e.school_id
    JOIN teachers t ON t.id = ta.teacher_id
    WHERE e.student_id = sid AND e.school_id = rls_school()
      AND e.status = 'ACTIVE' AND t.user_id = rls_uid()
  )
$$;

CREATE OR REPLACE FUNCTION rls_student_class(cid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT cid IS NOT NULL AND EXISTS (
    SELECT 1 FROM enrollments e
    JOIN students s ON s.id = e.student_id
    WHERE e.class_id = cid AND e.school_id = rls_school()
      AND e.status = 'ACTIVE' AND s.user_id = rls_uid()
  )
$$;

CREATE OR REPLACE FUNCTION rls_parent_class(cid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT cid IS NOT NULL AND EXISTS (
    SELECT 1 FROM enrollments e
    JOIN student_parents sp ON sp.student_id = e.student_id AND sp.school_id = e.school_id
    JOIN parents p ON p.id = sp.parent_id
    WHERE e.class_id = cid AND e.school_id = rls_school()
      AND e.status = 'ACTIVE' AND p.user_id = rls_uid()
  )
$$;

CREATE OR REPLACE FUNCTION rls_parent_owns(pid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT pid IS NOT NULL AND EXISTS (
    SELECT 1 FROM parents p
    WHERE p.id = pid AND p.user_id = rls_uid() AND p.school_id = rls_school()
  )
$$;

CREATE OR REPLACE FUNCTION rls_same_school_user(uid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT uid IS NOT NULL AND rls_school() IS NOT NULL AND EXISTS (
    SELECT 1 FROM school_memberships m
    WHERE m.user_id = uid AND m.school_id = rls_school() AND m.status = 'ACTIVE'
  )
$$;

CREATE OR REPLACE FUNCTION rls_user_row_writable(uid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT rls_role() = 'PLATFORM_ADMIN'
     OR uid = rls_uid()
     OR (
       rls_role() IN ('SCHOOL_ADMIN', 'HR')
       AND rls_school() IS NOT NULL
       AND NOT EXISTS (
         SELECT 1 FROM school_memberships m
         WHERE m.user_id = uid
           AND m.status = 'ACTIVE'
           AND m.school_id IS DISTINCT FROM rls_school()
       )
     )
$$;
"""

# Restrictive actor policies. Expression is used for both USING and WITH CHECK
# unless a table is listed in _CUSTOM.
_ACTOR: dict[str, str] = {
    "students": """
        rls_privileged() OR rls_role() = 'FINANCE'
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(id))
    """,
    "parents": """
        rls_privileged() OR rls_role() = 'FINANCE'
        OR (rls_role() = 'PARENT' AND user_id = rls_uid())
        OR (rls_role() = 'STUDENT' AND EXISTS (
            SELECT 1 FROM student_parents sp
            WHERE sp.parent_id = parents.id AND rls_student_self(sp.student_id)
        ))
        OR (rls_role() = 'TEACHER' AND EXISTS (
            SELECT 1 FROM student_parents sp
            WHERE sp.parent_id = parents.id AND rls_teacher_student(sp.student_id)
        ))
    """,
    "student_parents": """
        rls_privileged() OR rls_role() = 'FINANCE'
        OR (rls_role() = 'PARENT' AND rls_parent_owns(parent_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
    """,
    "classes": """
        rls_privileged() OR rls_role() IN ('FINANCE', 'HR')
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(id))
        OR (rls_role() = 'PARENT' AND rls_parent_class(id))
    """,
    "enrollments": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "attendance": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "homework": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(class_id) AND status <> 'DRAFT')
        OR (rls_role() = 'PARENT' AND rls_parent_class(class_id) AND status <> 'DRAFT')
    """,
    "homework_submissions": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "exams": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
        OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
    """,
    "results": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "timetable_entries": """
        rls_privileged() OR rls_role() = 'HR'
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
        OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
    """,
    "teaching_records": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
        OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
    """,
    "teacher_assignments": """
        rls_privileged() OR rls_role() = 'HR'
        OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
        OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
        OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
    """,
    "achievements": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "learning_sessions": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "student_progress": """
        rls_privileged()
        OR (rls_role() = 'TEACHER' AND rls_teacher_student(student_id))
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "student_ai_memory": """
        rls_privileged()
        OR (rls_role() = 'STUDENT' AND rls_student_self(student_id))
        OR (rls_role() = 'PARENT' AND rls_parent_child(student_id))
    """,
    "teachers": """
        rls_privileged() OR rls_role() IN ('HR', 'PAYROLL_APPROVER', 'TEACHER')
        OR (rls_role() = 'STUDENT' AND EXISTS (
            SELECT 1 FROM teacher_assignments ta
            WHERE ta.teacher_id = teachers.id AND rls_student_class(ta.class_id)
        ))
        OR (rls_role() = 'PARENT' AND EXISTS (
            SELECT 1 FROM teacher_assignments ta
            WHERE ta.teacher_id = teachers.id AND rls_parent_class(ta.class_id)
        ))
    """,
    "questions": "rls_role() IN ('PLATFORM_ADMIN','SCHOOL_ADMIN','TEACHER','STUDENT','PARENT')",
    "syllabus": "rls_role() IN ('PLATFORM_ADMIN','SCHOOL_ADMIN','TEACHER','STUDENT','PARENT')",
    "documents": """
        rls_role() IN ('PLATFORM_ADMIN','SCHOOL_ADMIN','TEACHER','STUDENT','PARENT')
        AND (
            class_id IS NULL OR rls_privileged()
            OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
            OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
            OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
        )
    """,
    "knowledge_documents": """
        rls_role() IN ('PLATFORM_ADMIN','SCHOOL_ADMIN','TEACHER','STUDENT','PARENT')
        AND (
            class_id IS NULL OR rls_privileged()
            OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
            OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
            OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
        )
    """,
    "knowledge_chunks": """
        rls_role() IN ('PLATFORM_ADMIN','SCHOOL_ADMIN','TEACHER','STUDENT','PARENT')
        AND (
            class_id IS NULL OR rls_privileged()
            OR (rls_role() = 'TEACHER' AND rls_teacher_class(class_id))
            OR (rls_role() = 'STUDENT' AND rls_student_class(class_id))
            OR (rls_role() = 'PARENT' AND rls_parent_class(class_id))
        )
    """,
    "subscriptions": "rls_privileged()",
    "invitations": "rls_privileged()",
}


def _enable(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def _policy(table: str, name: str, using: str, check: str | None = None, restrictive: bool = False) -> None:
    check_sql = check if check is not None else using
    kind = "AS RESTRICTIVE" if restrictive else ""
    op.execute(f"DROP POLICY IF EXISTS {name} ON {table}")
    op.execute(
        f"CREATE POLICY {name} ON {table} {kind} USING ({using}) WITH CHECK ({check_sql})"
    )


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_app') THEN
                CREATE ROLE sybeez_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END $$;
        """
    )
    for statement in [part.strip() for part in _FUNCTIONS.split("CREATE OR REPLACE FUNCTION ") if part.strip()]:
        op.execute("CREATE OR REPLACE FUNCTION " + statement)

    bind = op.get_bind()
    rows = bind.execute(
        __import__("sqlalchemy").text(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'school_id'
            ORDER BY table_name
            """
        )
    )
    tables = [row[0] for row in rows if row[0].replace("_", "").isalnum()]

    for table in tables:
        _enable(table)
        _policy(table, "tenant_isolation", "rls_tenant(school_id)")
        if table in _ACTOR:
            _policy(table, "actor_scope", _ACTOR[table], restrictive=True)

    _policy(
        "announcements",
        "actor_scope",
        """
        (is_published OR rls_privileged() OR author_id = rls_uid())
        AND (
            rls_privileged()
            OR (rls_role() IN ('FINANCE','HR','PAYROLL_APPROVER') AND class_id IS NULL AND is_published)
            OR (rls_role() = 'TEACHER' AND (class_id IS NULL OR rls_teacher_class(class_id)))
            OR (rls_role() = 'STUDENT' AND is_published AND (class_id IS NULL OR rls_student_class(class_id)))
            OR (rls_role() = 'PARENT' AND is_published AND (class_id IS NULL OR rls_parent_class(class_id)))
        )
        """,
        restrictive=True,
    )
    _policy(
        "messages",
        "actor_scope",
        "rls_privileged() OR sender_id = rls_uid() OR recipient_id = rls_uid()",
        check="rls_privileged() OR sender_id = rls_uid() OR recipient_id = rls_uid()",
        restrictive=True,
    )
    _policy(
        "membership_permission_overrides",
        "actor_scope",
        "rls_privileged() OR membership_id = rls_membership()",
        check="rls_privileged()",
        restrictive=True,
    )
    _policy(
        "notifications",
        "actor_scope",
        "rls_privileged() OR user_id = rls_uid()",
        restrictive=True,
    )
    _policy(
        "ai_conversations",
        "actor_scope",
        """
        rls_privileged() OR user_id = rls_uid()
        OR (student_id IS NOT NULL AND rls_role() = 'PARENT' AND rls_parent_child(student_id))
        """,
        restrictive=True,
    )
    _policy(
        "ai_messages",
        "actor_scope",
        """
        EXISTS (
            SELECT 1 FROM ai_conversations c
            WHERE c.id = ai_messages.conversation_id
        )
        """,
        restrictive=True,
    )
    _policy(
        "school_memberships",
        "actor_scope",
        """
        rls_role() = 'PLATFORM_ADMIN'
        OR (school_id = rls_school() AND (user_id = rls_uid() OR rls_role() IN ('SCHOOL_ADMIN', 'HR')))
        """,
        check="""
        rls_role() = 'PLATFORM_ADMIN'
        OR (school_id = rls_school() AND rls_role() IN ('SCHOOL_ADMIN', 'HR'))
        """,
        restrictive=True,
    )
    _policy(
        "audit_logs",
        "actor_scope",
        """
        rls_role() = 'PLATFORM_ADMIN'
        OR (rls_role() = 'SCHOOL_ADMIN' AND school_id IS NOT DISTINCT FROM rls_school())
        """,
        check="""
        rls_role() = 'PLATFORM_ADMIN'
        OR (school_id = rls_school() AND actor_id = rls_uid())
        """,
        restrictive=True,
    )
    _policy(
        "security_events",
        "actor_scope",
        """
        rls_role() = 'PLATFORM_ADMIN'
        OR (rls_role() = 'SCHOOL_ADMIN' AND school_id IS NOT DISTINCT FROM rls_school())
        """,
        check="""
        rls_role() = 'PLATFORM_ADMIN'
        OR (school_id = rls_school() AND (user_id IS NULL OR user_id = rls_uid()))
        """,
        restrictive=True,
    )

    _enable("schools")
    _policy(
        "schools",
        "tenant_isolation",
        "rls_role() = 'PLATFORM_ADMIN' OR id = rls_school()",
    )
    _enable("users")
    _policy(
        "users",
        "tenant_isolation",
        """
        rls_role() = 'PLATFORM_ADMIN'
        OR id = rls_uid()
        OR rls_same_school_user(id)
        OR (
            NULLIF(current_setting('app.lookup_email', true), '') IS NOT NULL
            AND lower(email) = lower(current_setting('app.lookup_email', true))
        )
        """,
        check="rls_user_row_writable(id)",
    )
    _enable("syllabus_topics")
    _policy(
        "syllabus_topics",
        "tenant_isolation",
        "EXISTS (SELECT 1 FROM syllabus s WHERE s.id = syllabus_topics.syllabus_id)",
    )

    op.execute("GRANT USAGE ON SCHEMA public TO sybeez_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sybeez_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sybeez_app")
    op.execute("REVOKE ALL ON TABLE alembic_version FROM sybeez_app")
    op.execute(
        """
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sybeez_app
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_protect_new_table() RETURNS event_trigger
        LANGUAGE plpgsql AS $fn$
        DECLARE
            cmd record;
            sch text;
            rel text;
            has_school boolean;
        BEGIN
            FOR cmd IN
                SELECT * FROM pg_event_trigger_ddl_commands() WHERE command_tag = 'CREATE TABLE'
            LOOP
                sch := split_part(cmd.object_identity, '.', 1);
                rel := split_part(cmd.object_identity, '.', 2);
                IF sch <> 'public' OR rel IN ('alembic_version', '') THEN
                    CONTINUE;
                END IF;
                EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE %I.%I TO sybeez_app', sch, rel);
                SELECT EXISTS (
                    SELECT 1 FROM pg_attribute a
                    WHERE a.attrelid = cmd.objid AND a.attname = 'school_id'
                      AND a.attnum > 0 AND NOT a.attisdropped
                ) INTO has_school;
                IF has_school THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', sch, rel);
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY', sch, rel);
                    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I.%I', sch, rel);
                    EXECUTE format(
                        'CREATE POLICY tenant_isolation ON %I.%I USING (rls_tenant(school_id)) WITH CHECK (rls_tenant(school_id))',
                        sch, rel
                    );
                END IF;
            END LOOP;
        END;
        $fn$
        """
    )
    # CREATE EVENT TRIGGER cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        op.execute("DROP EVENT TRIGGER IF EXISTS rls_protect_new_table")
        op.execute(
            """
            CREATE EVENT TRIGGER rls_protect_new_table ON ddl_command_end
            WHEN TAG IN ('CREATE TABLE')
            EXECUTE FUNCTION rls_protect_new_table()
            """
        )


def downgrade() -> None:
    op.execute("DROP EVENT TRIGGER IF EXISTS rls_protect_new_table")
    op.execute("DROP FUNCTION IF EXISTS rls_protect_new_table()")
    op.execute(
        """
        DO $$ DECLARE r record;
        BEGIN
            FOR r IN SELECT schemaname, tablename, policyname FROM pg_policies WHERE schemaname = 'public'
            LOOP
                EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
            END LOOP;
            FOR r IN
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
            LOOP
                EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', r.relname);
                EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', r.relname);
            END LOOP;
        END $$;
        """
    )
    for name in (
        "rls_user_row_writable(uuid)",
        "rls_same_school_user(uuid)",
        "rls_parent_owns(uuid)",
        "rls_parent_class(uuid)",
        "rls_student_class(uuid)",
        "rls_teacher_student(uuid)",
        "rls_parent_child(uuid)",
        "rls_student_self(uuid)",
        "rls_teacher_class(uuid)",
        "rls_privileged()",
        "rls_tenant(uuid)",
        "rls_membership()",
        "rls_school()",
        "rls_uid()",
        "rls_role()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {name}")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM sybeez_app")
    op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM sybeez_app")
    op.execute("REVOKE USAGE ON SCHEMA public FROM sybeez_app")
    op.execute("DROP ROLE IF EXISTS sybeez_app")
