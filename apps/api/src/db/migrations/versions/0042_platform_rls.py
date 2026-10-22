"""Platform schema: tenant RLS, role policies, indexes, pgvector, S3-ready keys.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-10

Renumbered from 0029 so it does not collide with 0029_hr_employees.
Payroll revision 0040 already forced school and role policies on payroll
tables. Those policies stay; this revision still forces RLS on them.

One public schema. Every tenant table has school_id. RLS is enabled and forced.
Policies fail closed when app.school_id, app.user_id, or app.role is unset.
The API login role is sybeez_app (NOBYPASSRLS). Helper functions are owned by
sybeez_definer (BYPASSRLS, NOLOGIN) so they can resolve parent/teacher scope
without recursing into the policies.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0042"
down_revision: Union[str, None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# No school_id, and not tenant data.
GLOBAL_TABLES = {
    "users",
    "refresh_tokens",
    "schools",
    "platform_plans",
    "platform_settings",
    "platform_ai_settings",
    "alembic_version",
}

FINANCE_TABLES = {
    "fee_adjustments",
    "fee_audit_logs",
    "fee_categories",
    "fee_concession_requests",
    "fee_concession_rules",
    "fee_day_closes",
    "fee_fine_waiver_requests",
    "fee_installments",
    "fee_invoice_items",
    "fee_invoices",
    "fee_ledger_entries",
    "fee_payment_allocations",
    "fee_payment_refunds",
    "fee_payment_tenders",
    "fee_payments",
    "fee_receipts",
    "fee_reminder_logs",
    "fee_sequences",
    "fee_settings",
    "fee_structure_items",
    "fee_structures",
    "fee_webhook_events",
    "finance_approvals",
    "finance_staff_scopes",
    "financial_years",
    "late_fee_rules",
    "school_branches",
    "sibling_groups",
    "student_fee_accounts",
    "student_fee_profiles",
}

PEOPLE_TABLES = {
    "students",
    "parents",
    "teachers",
    "enrollments",
    "student_parents",
    "classes",
    "teacher_assignments",
}

USER_PRIVATE_TABLES = {
    "personal_notes",
    "personal_goals",
    "personal_goal_milestones",
    "personal_habits",
    "personal_habit_checkins",
    "diary_entries",
    "notifications",
    "notification_deliveries",
    "notification_preferences",
    "ai_conversations",
    "ai_messages",
    "messages",
    "practice_attempts",
}

# Participants of a conversation, not the whole school.
INBOX_TABLES = {
    "conversations",
    "conversation_participants",
    "conversation_messages",
}

# School-wide reference data even when a user_id column is present.
CATALOG_TABLES = {
    "announcement_audiences",
}

# Null school_id is the user's own row (preferences, practice), not a cross-school leak.
USER_NULL_SCHOOL = {
    "notifications",
    "notification_deliveries",
    "notification_preferences",
    "practice_attempts",
}

AUDIT_TABLES = {
    "audit_logs",
    "security_events",
    "report_audit_logs",
    "communication_audit_logs",
    "attendance_audit_logs",
    "result_audit_logs",
}

STAFF_TABLES = {
    "payroll_payslips",
    "payroll_runs",
    "staff_leave_requests",
    "platform_school_access",
    "platform_ai_usage_daily",
    "invitations",
}

# People-ops tables created by HRM revisions 0029–0035. They often have user_id,
# which would otherwise classify them as user-private and hide them from HR.
HR_TABLES = {
    "employees",
    "departments",
    "designations",
    "employee_documents",
    "hr_letters",
    "hr_requests",
    "hr_request_events",
    "hr_settings",
    "hr_workflows",
    "hr_workflow_tasks",
    "staff_attendance",
}

# 0040 already installed school + payroll role policies. Replacing them with
# app.can_see would drop payroll_is_staff / payroll_owns_* access.
PAYROLL_LOCKED = {
    "payroll_school_settings",
    "payroll_setting_revisions",
    "salary_components",
    "salary_templates",
    "salary_template_lines",
    "employee_salary_assignments",
    "employee_statutory_profiles",
    "employee_bank_accounts",
    "school_holidays",
    "weekly_off_rules",
    "pt_slab_rules",
    "payroll_loans",
    "payroll_arrears",
    "payroll_reimbursements",
    "tax_declarations",
    "tax_declaration_proofs",
    "payroll_payslip_lines",
    "payroll_payments",
    "payroll_audit_logs",
    "payroll_settlements",
    "payroll_compliance_events",
    "payroll_role_grants",
    "staff_attendance_sessions",
    "staff_attendance_regularisations",
    "staff_attendance_period_locks",
    "payroll_runs",
    "payroll_payslips",
    "staff_attendance",
    "staff_leave_requests",
}

# student_id expression when the table does not have that column.
STUDENT_EXPR = {
    "students": "id",
    "fee_invoices": "app.invoice_student(id)",
    "fee_invoice_items": "app.invoice_student(invoice_id)",
    "fee_payments": "app.payment_student(id)",
    "fee_receipts": "app.payment_student(payment_id)",
    "fee_payment_allocations": "app.payment_student(payment_id)",
    "fee_payment_tenders": "app.payment_student(payment_id)",
    "fee_payment_refunds": "app.payment_student(payment_id)",
    "fee_adjustments": "app.account_student(student_fee_account_id)",
    "fee_reminder_logs": "app.invoice_student(invoice_id)",
    "submission_attachments": "app.submission_student(submission_id)",
}

CLASS_EXPR = {
    "classes": "id",
    "homework_attachments": "app.homework_class(homework_id)",
    # Inbox policies pass the conversation id in the class slot.
    "conversations": "id",
    "conversation_participants": "conversation_id",
    "conversation_messages": "conversation_id",
}

USER_EXPR = {
    "personal_goal_milestones": "app.goal_user(goal_id)",
    "ai_messages": "app.ai_conversation_user(conversation_id)",
    "messages": "app.message_party(id)",
    "practice_attempts": "student_user_id",
}

FUNCTIONS_SQL = r"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS app;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
    CREATE ROLE sybeez_definer NOLOGIN BYPASSRLS;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION app.account_student(aid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT student_id FROM student_fee_accounts WHERE id = aid
$$;

CREATE OR REPLACE FUNCTION app.invoice_student(iid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT a.student_id
  FROM fee_invoices i
  JOIN student_fee_accounts a ON a.id = i.student_fee_account_id
  WHERE i.id = iid
$$;

CREATE OR REPLACE FUNCTION app.payment_student(pid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT a.student_id
  FROM fee_payments p
  JOIN student_fee_accounts a ON a.id = p.student_fee_account_id
  WHERE p.id = pid
$$;

CREATE OR REPLACE FUNCTION app.homework_class(hid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT class_id FROM homework WHERE id = hid
$$;

CREATE OR REPLACE FUNCTION app.submission_student(sid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT student_id FROM homework_submissions WHERE id = sid
$$;

CREATE OR REPLACE FUNCTION app.goal_user(gid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT user_id FROM personal_goals WHERE id = gid
$$;

CREATE OR REPLACE FUNCTION app.ai_conversation_user(cid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT user_id FROM ai_conversations WHERE id = cid
$$;

CREATE OR REPLACE FUNCTION app.conversation_member(cid uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT EXISTS (
    SELECT 1
    FROM conversation_participants cp
    WHERE cp.conversation_id = cid
      AND cp.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
  )
$$;

CREATE OR REPLACE FUNCTION app.message_party(mid uuid) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT CASE
    WHEN m.sender_id = NULLIF(current_setting('app.user_id', true), '')::uuid
      OR m.recipient_id = NULLIF(current_setting('app.user_id', true), '')::uuid
    THEN NULLIF(current_setting('app.user_id', true), '')::uuid
    ELSE NULL
  END
  FROM messages m
  WHERE m.id = mid
$$;

CREATE OR REPLACE FUNCTION app.can_see(
  category text,
  row_student uuid,
  row_class uuid,
  row_user uuid
) RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  r text := NULLIF(current_setting('app.role', true), '');
  uid uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
  sid uuid := NULLIF(current_setting('app.school_id', true), '')::uuid;
BEGIN
  IF current_setting('app.rls_bypass', true) = 'on' THEN
    RETURN true;
  END IF;
  IF r IS NULL OR sid IS NULL THEN
    RETURN false;
  END IF;
  IF r IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE') THEN
    RETURN true;
  END IF;
  IF uid IS NULL THEN
    RETURN false;
  END IF;

  IF category = 'catalog' THEN
    RETURN r IN ('TEACHER', 'STUDENT', 'PARENT', 'ACCOUNTANT', 'CASHIER', 'AUDITOR');
  END IF;

  IF category = 'audit' THEN
    RETURN r IN ('AUDITOR', 'ACCOUNTANT');
  END IF;

  IF category = 'staff' THEN
    RETURN r IN (
      'HR_MANAGER', 'PAYROLL_ADMIN', 'PAYROLL_APPROVER', 'ACCOUNTANT', 'AUDITOR'
    );
  END IF;

  IF category = 'user_private' THEN
    RETURN row_user IS NOT NULL AND row_user = uid;
  END IF;

  IF category = 'inbox' THEN
    IF row_user IS NOT NULL AND row_user = uid THEN
      RETURN true;
    END IF;
    IF row_class IS NOT NULL THEN
      RETURN app.conversation_member(row_class);
    END IF;
    RETURN false;
  END IF;

  IF category = 'finance' THEN
    IF r IN ('ACCOUNTANT', 'CASHIER', 'AUDITOR') THEN
      RETURN true;
    END IF;
    IF r = 'STUDENT' THEN
      RETURN row_student IS NOT NULL AND row_student = (
        SELECT s.id FROM students s WHERE s.user_id = uid AND s.school_id = sid LIMIT 1
      );
    END IF;
    IF r = 'PARENT' THEN
      RETURN row_student IS NOT NULL AND EXISTS (
        SELECT 1
        FROM student_parents sp
        JOIN parents p ON p.id = sp.parent_id
        WHERE sp.student_id = row_student
          AND p.user_id = uid
          AND sp.school_id = sid
          AND p.school_id = sid
      );
    END IF;
    RETURN false;
  END IF;

  IF category = 'people' OR category = 'student_data' THEN
    IF r IN ('ACCOUNTANT', 'CASHIER', 'AUDITOR') AND category = 'people' THEN
      RETURN true;
    END IF;
    IF r = 'STUDENT' THEN
      -- A row about a student is only that student's, even when it also has a class.
      IF row_student IS NOT NULL THEN
        RETURN row_student = (
          SELECT s.id FROM students s WHERE s.user_id = uid AND s.school_id = sid LIMIT 1
        );
      END IF;
      IF row_class IS NOT NULL THEN
        RETURN row_class IN (
          SELECT e.class_id
          FROM enrollments e
          JOIN students s ON s.id = e.student_id
          WHERE s.user_id = uid AND e.school_id = sid AND e.status = 'ACTIVE'
        );
      END IF;
      RETURN row_user = uid;
    END IF;
    IF r = 'PARENT' THEN
      IF row_student IS NOT NULL THEN
        RETURN EXISTS (
          SELECT 1
          FROM student_parents sp
          JOIN parents p ON p.id = sp.parent_id
          WHERE sp.student_id = row_student
            AND p.user_id = uid
            AND sp.school_id = sid
            AND p.school_id = sid
        );
      END IF;
      IF row_class IS NOT NULL THEN
        RETURN EXISTS (
          SELECT 1
          FROM enrollments e
          JOIN student_parents sp ON sp.student_id = e.student_id
          JOIN parents p ON p.id = sp.parent_id
          WHERE e.class_id = row_class
            AND p.user_id = uid
            AND e.school_id = sid
            AND e.status = 'ACTIVE'
        );
      END IF;
      RETURN row_user = uid;
    END IF;
    IF r = 'TEACHER' THEN
      RETURN row_user = uid
        OR (
          row_student IS NOT NULL AND EXISTS (
            SELECT 1
            FROM enrollments e
            JOIN teacher_assignments ta ON ta.class_id = e.class_id AND ta.school_id = e.school_id
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE e.student_id = row_student
              AND t.user_id = uid
              AND e.school_id = sid
              AND e.status = 'ACTIVE'
          )
        )
        OR (
          row_class IS NOT NULL AND EXISTS (
            SELECT 1
            FROM teacher_assignments ta
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE ta.class_id = row_class AND t.user_id = uid AND ta.school_id = sid
          )
        );
    END IF;
    RETURN false;
  END IF;

  IF category = 'class_content' THEN
    IF row_class IS NULL THEN
      RETURN r IN ('TEACHER', 'STUDENT', 'PARENT');
    END IF;
    IF r = 'TEACHER' THEN
      RETURN EXISTS (
        SELECT 1
        FROM teacher_assignments ta
        JOIN teachers t ON t.id = ta.teacher_id
        WHERE ta.class_id = row_class AND t.user_id = uid AND ta.school_id = sid
      );
    END IF;
    IF r = 'STUDENT' THEN
      RETURN EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN students s ON s.id = e.student_id
        WHERE e.class_id = row_class AND s.user_id = uid AND e.school_id = sid AND e.status = 'ACTIVE'
      );
    END IF;
    IF r = 'PARENT' THEN
      RETURN EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN student_parents sp ON sp.student_id = e.student_id
        JOIN parents p ON p.id = sp.parent_id
        WHERE e.class_id = row_class
          AND p.user_id = uid
          AND e.school_id = sid
          AND e.status = 'ACTIVE'
      );
    END IF;
    RETURN false;
  END IF;

  RETURN false;
END;
$fn$;
"""


def _split_sql(sql: str) -> list[str]:
    """Split a script on semicolons that sit outside dollar-quoted bodies.

    asyncpg prepares one command at a time and rejects a multi-command string.
    """
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    dollar: str | None = None
    while i < len(sql):
        if dollar is not None:
            if sql.startswith(dollar, i):
                buf.append(dollar)
                i += len(dollar)
                dollar = None
                continue
            buf.append(sql[i])
            i += 1
            continue
        if sql[i] == "$":
            j = i + 1
            while j < len(sql) and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < len(sql) and sql[j] == "$":
                dollar = sql[i : j + 1]
                buf.append(dollar)
                i = j + 1
                continue
        if sql[i] == ";":
            statement = "".join(buf).strip()
            if statement:
                parts.append(statement)
            buf = []
            i += 1
            continue
        buf.append(sql[i])
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def _exec(sql: str) -> None:
    for statement in _split_sql(sql):
        op.execute(statement)


def _category(table: str, columns: set[str]) -> str:
    if table in CATALOG_TABLES:
        return "catalog"
    if table in FINANCE_TABLES:
        return "finance"
    if table in PEOPLE_TABLES:
        return "people"
    if table in INBOX_TABLES:
        return "inbox"
    if table in USER_PRIVATE_TABLES:
        return "user_private"
    if table in AUDIT_TABLES:
        return "audit"
    if table in HR_TABLES or table in STAFF_TABLES:
        return "staff"
    if "student_id" in columns or table in STUDENT_EXPR:
        return "student_data"
    if "class_id" in columns or table in CLASS_EXPR:
        return "class_content"
    if "user_id" in columns or table in USER_EXPR:
        return "user_private"
    return "catalog"


def _expr(table: str, columns: set[str], column: str, special: dict[str, str]) -> str:
    if table in special:
        return special[table]
    if column in columns:
        return column
    return "NULL::uuid"


def upgrade() -> None:
    _exec(FUNCTIONS_SQL)
    op.execute("ALTER TABLE schools ALTER COLUMN id SET DEFAULT gen_random_uuid()")

    _exec(
        """
        ALTER TABLE syllabus_topics ADD COLUMN IF NOT EXISTS school_id uuid;
        UPDATE syllabus_topics t
           SET school_id = s.school_id
          FROM syllabus s
         WHERE t.syllabus_id = s.id AND t.school_id IS NULL;
        ALTER TABLE personal_goal_milestones ADD COLUMN IF NOT EXISTS school_id uuid;
        UPDATE personal_goal_milestones m
           SET school_id = g.school_id
          FROM personal_goals g
         WHERE m.goal_id = g.id AND m.school_id IS NULL;
        ALTER TABLE personal_habit_checkins ADD COLUMN IF NOT EXISTS school_id uuid;
        UPDATE personal_habit_checkins c
           SET school_id = h.school_id
          FROM personal_habits h
         WHERE c.habit_id = h.id AND c.school_id IS NULL;
        """
    )
    for table in ("syllabus_topics", "personal_goal_milestones", "personal_habit_checkins"):
        op.execute(f"ALTER TABLE {table} ALTER COLUMN school_id SET NOT NULL")
        op.execute(
            f"""
            DO $$
            BEGIN
              ALTER TABLE {table}
                ADD CONSTRAINT fk_{table}_school_id_schools
                FOREIGN KEY (school_id) REFERENCES schools(id) ON DELETE CASCADE;
            EXCEPTION WHEN duplicate_object OR duplicate_table THEN
              NULL;
            END $$;
            """
        )

    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
        SELECT c.relname AS table_name,
               a.attname AS column_name,
               a.attnotnull AS not_null
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        """
        )
    ).fetchall()
    columns: dict[str, set[str]] = {}
    for table_name, column_name, _not_null in rows:
        columns.setdefault(table_name, set()).add(column_name)

    for name in (
        "app.can_see(text, uuid, uuid, uuid)",
        "app.account_student(uuid)",
        "app.invoice_student(uuid)",
        "app.payment_student(uuid)",
        "app.homework_class(uuid)",
        "app.submission_student(uuid)",
        "app.goal_user(uuid)",
        "app.ai_conversation_user(uuid)",
        "app.conversation_member(uuid)",
        "app.message_party(uuid)",
    ):
        op.execute(f"ALTER FUNCTION {name} OWNER TO sybeez_definer")
        op.execute(f"GRANT EXECUTE ON FUNCTION {name} TO sybeez_app")

    op.execute("GRANT USAGE ON SCHEMA app TO sybeez_app")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO sybeez_definer")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sybeez_app"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sybeez_app")

    _exec(
        """
        ALTER TABLE schools ENABLE ROW LEVEL SECURITY;
        ALTER TABLE schools FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS schools_tenant ON schools;
        CREATE POLICY schools_tenant ON schools
        USING (
          current_setting('app.role', true) IN ('PLATFORM_ADMIN', 'SERVICE')
          OR id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
          current_setting('app.role', true) = 'PLATFORM_ADMIN'
          OR (
            current_setting('app.role', true) IN ('SCHOOL_ADMIN', 'SERVICE')
            AND id = NULLIF(current_setting('app.school_id', true), '')::uuid
          )
        );
        """
    )
    _exec(
        """
        ALTER TABLE school_memberships ENABLE ROW LEVEL SECURITY;
        ALTER TABLE school_memberships FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS school_memberships_tenant ON school_memberships;
        DROP POLICY IF EXISTS school_memberships_role ON school_memberships;
        CREATE POLICY school_memberships_tenant ON school_memberships
        USING (
          user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
          OR (
            school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            AND current_setting('app.role', true) IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')
          )
        )
        WITH CHECK (
          school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
          AND current_setting('app.role', true) IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')
        );
        """
    )

    for table, cols in sorted(columns.items()):
        if table in GLOBAL_TABLES or table == "school_memberships" or "school_id" not in cols:
            continue
        if table in PAYROLL_LOCKED:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            continue
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        school_using = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
        school_check = school_using
        if table in USER_NULL_SCHOOL:
            owner_col = "student_user_id" if table == "practice_attempts" else "user_id"
            school_using = (
                "(school_id = NULLIF(current_setting('app.school_id', true), '')::uuid "
                f"OR (school_id IS NULL AND {owner_col} = "
                "NULLIF(current_setting('app.user_id', true), '')::uuid))"
            )
            school_check = school_using
        if table == "fee_webhook_events":
            school_using = (
                "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid "
                "OR (school_id IS NULL AND current_setting('app.rls_bypass', true) = 'on')"
            )
            school_check = (
                "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid "
                "OR school_id IS NULL"
            )
        op.execute(
            f"""
            CREATE POLICY {table}_school ON {table}
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR {school_using}
            )
            WITH CHECK ({school_check})
            """
        )
        student = _expr(table, cols, "student_id", STUDENT_EXPR)
        klass = _expr(table, cols, "class_id", CLASS_EXPR)
        user = _expr(table, cols, "user_id", USER_EXPR)
        category = _category(table, cols)
        op.execute(
            f"""
            CREATE POLICY {table}_role ON {table} AS RESTRICTIVE
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR app.can_see('{category}', {student}, {klass}, {user})
            )
            WITH CHECK (app.can_see('{category}', {student}, {klass}, {user}))
            """
        )
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_school ON {table} (school_id)")
        if "student_id" in cols:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_school_student ON {table} (school_id, student_id)"
            )
        if "created_at" in cols:
            op.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_school_created ON {table} (school_id, created_at)"
            )

    # 0040 created these before sybeez_definer existed on a fresh database.
    for name in (
        "app.payroll_is_staff()",
        "app.payroll_owns_employee(uuid)",
        "app.payroll_owns_payslip(uuid)",
        "app.payroll_owns_declaration(uuid)",
    ):
        op.execute(
            f"""
            DO $$ BEGIN
              ALTER FUNCTION {name} OWNER TO sybeez_definer;
            EXCEPTION WHEN undefined_function THEN NULL;
            END $$
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = [
        row[0]
        for row in bind.execute(
            text(
                """
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            """
            )
        ).fetchall()
    ]
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
    op.execute("DROP POLICY IF EXISTS schools_tenant ON schools")
    op.execute("DROP POLICY IF EXISTS school_memberships_tenant ON school_memberships")
    for name in (
        "app.can_see(text, uuid, uuid, uuid)",
        "app.account_student(uuid)",
        "app.invoice_student(uuid)",
        "app.payment_student(uuid)",
        "app.homework_class(uuid)",
        "app.submission_student(uuid)",
        "app.goal_user(uuid)",
        "app.ai_conversation_user(uuid)",
        "app.conversation_member(uuid)",
        "app.message_party(uuid)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {name}")
