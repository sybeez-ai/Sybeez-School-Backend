"""HRM specification compliance: employment history, leave policy, attendance rules,
file verification/expiry, letter templates and richer letter types.

Adds:
  * employees: reporting manager, exit date/reason/notes; employee_history (effective-dated events)
  * staff_attendance: check-in/out times and the leave request a LEAVE mark came from
  * staff_leave_requests: leave type, working-day count, resubmission link
  * hr_leave_types, hr_holidays, hr_letter_templates (immutable versions)
  * employee_documents: verification status/actor/time/note, expiry date; TAX and DISCIPLINARY
  * hr_letters: wider letter_type (new letter types), template_version
  * hr_settings: employee code prefix, working days, work start time and late grace,
    leave rules, document expiry warning, onboarding/offboarding checklists

Every step is idempotent: ``0001_initial`` builds a fresh database from the current models,
so these objects may already exist when this revision runs.

Downgrade maps letter types and document categories that did not exist before back to
``OTHER`` (the previous catch-all) so the narrower checks can be restored; everything else
added here is dropped.

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-11

Renumbered from HRM 0037 so it follows 0051. New tables get the same forced
row-level security as 0042: a school policy plus a restrictive app.can_see
policy. Leave types and holidays stay readable by teacher and staff self-service.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.db.migration_guards import (
    column_exists,
    create_index_if_missing,
    index_exists,
    table_exists,
)

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_LETTER_TYPES = ("OFFER", "APPOINTMENT", "EXPERIENCE", "RELIEVING", "OTHER")
NEW_LETTER_TYPES = (
    "OFFER",
    "APPOINTMENT",
    "CONFIRMATION",
    "EMPLOYMENT_VERIFICATION",
    "SALARY_CERTIFICATE",
    "EXPERIENCE",
    "PROMOTION",
    "TRANSFER",
    "DESIGNATION_CHANGE",
    "INCREMENT",
    "RELIEVING",
    "OTHER",
)
OLD_CATEGORIES = ("ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "OTHER")
NEW_CATEGORIES = ("ID_PROOF", "CONTRACT", "QUALIFICATION", "MEDICAL", "BANK", "TAX", "DISCIPLINARY", "OTHER")
EXIT_REASONS = ("RESIGNATION", "TERMINATION", "RETIREMENT", "CONTRACT_END", "OTHER")
HISTORY_EVENTS = (
    "JOINED",
    "DEPARTMENT_CHANGED",
    "DESIGNATION_CHANGED",
    "MANAGER_CHANGED",
    "EMPLOYMENT_TYPE_CHANGED",
    "DEACTIVATED",
    "REACTIVATED",
    "EXITED",
    "REHIRED",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _constraint_exists(name: str) -> bool:
    return bool(
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_constraint WHERE conname = :n"), {"n": name}).first()
    )


def _add_check(table: str, name: str, expr: str) -> None:
    if not _constraint_exists(name):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr})")


def _replace_check(table: str, name: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr})")


def _add_fk(table: str, name: str, ddl: str) -> None:
    if not _constraint_exists(name):
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} {ddl}")


def _add_column(table: str, column: sa.Column) -> None:
    if not column_exists(table, column.name):
        op.add_column(table, column)


def _uuid_fk_users(name: str) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


# Tables this revision creates. 0042 already ran, so it cannot see them.
_NEW_TABLES = ("hr_leave_types", "hr_holidays", "hr_letter_templates", "employee_history")
# School calendar rows that teacher and staff self-service must read. Writes stay with HR.
_SELF_SERVICE_READ = {"hr_leave_types", "hr_holidays"}


def _install_hr_access() -> None:
    """Per-person hr.read / hr.manage grants, plus the hrm portal, for RLS."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.hr_access() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT current_setting('app.rls_bypass', true) = 'on'
            OR current_setting('app.role', true) IN (
              'SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'HR', 'HR_MANAGER'
            )
            OR (
              current_setting('app.role', true) = 'PLATFORM_STAFF'
              AND (rls_staff_portal('hrm') OR rls_staff_portal('school_admin'))
            )
            OR EXISTS (
              SELECT 1
              FROM school_memberships m
              JOIN membership_permission_overrides o
                ON o.membership_id = m.id AND o.school_id = m.school_id
              WHERE m.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                AND m.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
                AND m.status = 'ACTIVE'
                AND o.effect = 'GRANT'
                AND o.permission_code IN ('hr.read', 'hr.manage')
            )
        $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.hr_access() OWNER TO sybeez_definer;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.hr_access() TO sybeez_app")
    # can_see and payroll_is_staff are SECURITY DEFINER, owned by sybeez_definer.
    # That role has no usage on schema app (0042 grants it only to sybeez_app), so a
    # schema-qualified call to app.hr_access() fails with "permission denied for schema
    # app". membership_permission_overrides was created after 0042's table grants.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            GRANT USAGE ON SCHEMA app TO sybeez_definer;
            GRANT EXECUTE ON FUNCTION app.hr_access() TO sybeez_definer;
            GRANT SELECT ON school_memberships TO sybeez_definer;
            GRANT SELECT ON membership_permission_overrides TO sybeez_definer;
          END IF;
        END $$;
        """
    )


def _extend_can_see() -> None:
    """Teach the installed app.can_see about HR grants and the STAFF catalog role."""
    import re

    definition = op.get_bind().execute(
        sa.text("SELECT pg_get_functiondef('app.can_see(text,uuid,uuid,uuid)'::regprocedure)")
    ).scalar_one()
    staff_new = """IF category = 'staff' THEN
    IF app.hr_access() THEN
      RETURN true;
    END IF;
    RETURN r IN (
      'HR_MANAGER', 'HR', 'PAYROLL_ADMIN', 'PAYROLL_APPROVER', 'ACCOUNTANT', 'AUDITOR'
    );
  END IF;"""
    patched, staff_count = re.subn(
        r"IF category = 'staff' THEN\s+RETURN r IN \(\s*"
        r"'HR_MANAGER', 'HR', 'PAYROLL_ADMIN', 'PAYROLL_APPROVER', 'ACCOUNTANT', 'AUDITOR'\s*"
        r"\);\s+END IF;",
        staff_new,
        definition,
        count=1,
    )
    # The first "category = 'staff'" is the platform-staff portal check. The role
    # branch is "IF category = 'staff' THEN". Re-running this revision must notice
    # that branch already calls hr_access and leave it alone.
    staff_branch = patched.split("IF category = 'staff' THEN", 1)[-1][:800]
    if staff_count != 1 and "app.hr_access()" not in staff_branch:
        raise RuntimeError("Could not extend app.can_see staff branch for HR grants")
    catalog_new = (
        "RETURN r IN ('TEACHER', 'STAFF', 'STUDENT', 'PARENT', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE');"
    )
    if "'STAFF'" not in patched.split("category = 'catalog'", 1)[-1].split("END IF", 1)[0]:
        patched, catalog_count = re.subn(
            r"RETURN r IN \('TEACHER', 'STUDENT', 'PARENT', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE'\);",
            catalog_new,
            patched,
            count=1,
        )
        if catalog_count != 1:
            raise RuntimeError("Could not add STAFF to app.can_see catalog branch")
    op.execute(patched)
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.can_see(text, uuid, uuid, uuid) OWNER TO sybeez_definer;
          END IF;
        END $$;
        """
    )
    # CREATE OR REPLACE keeps grants, but re-assert them so a replaced body stays callable.
    op.execute("GRANT EXECUTE ON FUNCTION app.can_see(text, uuid, uuid, uuid) TO sybeez_app")


def _extend_payroll_is_staff() -> None:
    """HR memberships and hrm grants can see payroll-locked HR rows such as leave and attendance."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.payroll_is_staff() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT current_setting('app.rls_bypass', true) = 'on'
            OR current_setting('app.role', true) IN (
              'SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE',
              'PAYROLL_ADMIN', 'HR_MANAGER', 'HR', 'ACCOUNTANT', 'PAYROLL_APPROVER', 'AUDITOR'
            )
            OR app.hr_access()
            OR EXISTS (
              SELECT 1 FROM payroll_role_grants g
              WHERE g.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
                AND g.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                AND g.role IN ('PAYROLL_ADMIN', 'HR_MANAGER', 'HR', 'ACCOUNTANT', 'PAYROLL_APPROVER', 'AUDITOR')
                AND g.effective_from <= CURRENT_DATE
                AND (g.effective_to IS NULL OR g.effective_to >= CURRENT_DATE)
            )
        $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.payroll_is_staff() OWNER TO sybeez_definer;
          END IF;
        END $$;
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION app.payroll_is_staff() TO sybeez_app")


def _force_rls(table: str) -> None:
    if not table_exists(table):
        return
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_school ON {table}
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
          school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
    self_service = ""
    if table in _SELF_SERVICE_READ:
        self_service = "OR current_setting('app.role', true) IN ('TEACHER', 'STAFF')"
    op.execute(
        f"""
        CREATE POLICY {table}_role ON {table} AS RESTRICTIVE
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR app.can_see('staff', NULL::uuid, NULL::uuid, NULL::uuid)
          OR app.hr_access()
          {self_service}
        )
        WITH CHECK (
          app.can_see('staff', NULL::uuid, NULL::uuid, NULL::uuid)
          OR app.hr_access()
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")


def _seed_staff_permissions() -> None:
    from src.modules.access.catalog import ROLE_PERMISSIONS
    from src.shared.enums import UserRole

    bind = op.get_bind()
    for perm in ROLE_PERMISSIONS[UserRole.STAFF]:
        bind.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role, permission_code)
                VALUES ('STAFF', :perm)
                ON CONFLICT DO NOTHING
                """
            ),
            {"perm": perm},
        )


def _secure_new_hr_tables() -> None:
    _install_hr_access()
    _extend_can_see()
    _extend_payroll_is_staff()
    for table in _NEW_TABLES:
        _force_rls(table)
    _seed_staff_permissions()


def upgrade() -> None:
    # ── leave types (referenced by staff_leave_requests) ─────────────────────
    if not table_exists("hr_leave_types"):
        op.create_table(
            "hr_leave_types",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("code", sa.String(16), nullable=False),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("annual_quota", sa.Numeric(5, 1), nullable=True),
            sa.Column("accrual", sa.String(16), nullable=False, server_default="ANNUAL"),
            sa.Column("is_paid", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("max_consecutive_days", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
            _uuid_fk_users("created_by"),
            _uuid_fk_users("updated_by"),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name="pk_hr_leave_types"),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], name="fk_hr_leave_types_school_id_schools", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["users.id"], name="fk_hr_leave_types_created_by_users", ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["updated_by"], ["users.id"], name="fk_hr_leave_types_updated_by_users", ondelete="SET NULL"
            ),
            sa.UniqueConstraint("id", "school_id", name="uq_hr_leave_types_id_school_id"),
            sa.CheckConstraint("code ~ '^[A-Z0-9_]{1,16}$'", name="ck_hr_leave_types_code_format"),
            sa.CheckConstraint(
                "annual_quota IS NULL OR (annual_quota >= 0 AND annual_quota <= 366)",
                name="ck_hr_leave_types_quota_range",
            ),
            sa.CheckConstraint("accrual IN ('ANNUAL', 'MONTHLY')", name="ck_hr_leave_types_accrual_allowed"),
            sa.CheckConstraint(
                "max_consecutive_days IS NULL OR max_consecutive_days >= 1",
                name="ck_hr_leave_types_max_consecutive_positive",
            ),
        )
    create_index_if_missing("ix_hr_leave_types_school_id", "hr_leave_types", ["school_id"])
    if not index_exists("uq_hr_leave_types_school_code"):
        op.execute("CREATE UNIQUE INDEX uq_hr_leave_types_school_code ON hr_leave_types (school_id, lower(code))")

    # ── holidays ─────────────────────────────────────────────────────────────
    if not table_exists("hr_holidays"):
        op.create_table(
            "hr_holidays",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("holiday_date", sa.Date(), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            _uuid_fk_users("created_by"),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name="pk_hr_holidays"),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], name="fk_hr_holidays_school_id_schools", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["users.id"], name="fk_hr_holidays_created_by_users", ondelete="SET NULL"
            ),
            sa.UniqueConstraint("school_id", "holiday_date", name="uq_hr_holidays_school_date"),
        )
    create_index_if_missing("ix_hr_holidays_school_id", "hr_holidays", ["school_id"])

    # ── letter templates ─────────────────────────────────────────────────────
    if not table_exists("hr_letter_templates"):
        op.create_table(
            "hr_letter_templates",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("letter_type", sa.String(32), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            _uuid_fk_users("created_by"),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name="pk_hr_letter_templates"),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], name="fk_hr_letter_templates_school_id_schools", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["created_by"], ["users.id"], name="fk_hr_letter_templates_created_by_users", ondelete="SET NULL"
            ),
            sa.UniqueConstraint(
                "school_id", "letter_type", "version", name="uq_hr_letter_templates_school_type_version"
            ),
            sa.CheckConstraint(_in("letter_type", NEW_LETTER_TYPES), name="ck_hr_letter_templates_type_allowed"),
            sa.CheckConstraint("version >= 1", name="ck_hr_letter_templates_version_positive"),
        )
    create_index_if_missing("ix_hr_letter_templates_school_id", "hr_letter_templates", ["school_id"])

    # ── employees + history ──────────────────────────────────────────────────
    _add_column("employees", sa.Column("reporting_manager_id", postgresql.UUID(as_uuid=True), nullable=True))
    _add_column("employees", sa.Column("exit_date", sa.Date(), nullable=True))
    _add_column("employees", sa.Column("exit_reason", sa.String(32), nullable=True))
    _add_column("employees", sa.Column("exit_notes", sa.Text(), nullable=True))
    create_index_if_missing("ix_employees_reporting_manager_id", "employees", ["reporting_manager_id"])
    _add_fk(
        "employees",
        "fk_employees_manager_same_school",
        "FOREIGN KEY (reporting_manager_id, school_id) REFERENCES employees (id, school_id) "
        "ON DELETE SET NULL (reporting_manager_id)",
    )
    _add_check("employees", "ck_employees_manager_not_self", "reporting_manager_id IS NULL OR reporting_manager_id <> id")
    _add_check(
        "employees", "ck_employees_exit_reason_allowed", f"exit_reason IS NULL OR {_in('exit_reason', EXIT_REASONS)}"
    )

    if not table_exists("employee_history"):
        op.create_table(
            "employee_history",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("school_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("employee_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("event_type", sa.String(32), nullable=False),
            sa.Column("effective_date", sa.Date(), nullable=False),
            sa.Column("from_value", sa.String(255), nullable=True),
            sa.Column("to_value", sa.String(255), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            _uuid_fk_users("actor_user_id"),
            *_timestamps(),
            sa.PrimaryKeyConstraint("id", name="pk_employee_history"),
            sa.ForeignKeyConstraint(
                ["school_id"], ["schools.id"], name="fk_employee_history_school_id_schools", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["actor_user_id"], ["users.id"], name="fk_employee_history_actor_user_id_users", ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["employee_id", "school_id"],
                ["employees.id", "employees.school_id"],
                name="fk_employee_history_employee_same_school",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(_in("event_type", HISTORY_EVENTS), name="ck_employee_history_event_allowed"),
        )
    create_index_if_missing("ix_employee_history_school_id", "employee_history", ["school_id"])
    create_index_if_missing(
        "ix_employee_history_employee_effective", "employee_history", ["employee_id", "effective_date"]
    )

    # ── leave requests ───────────────────────────────────────────────────────
    _add_column("staff_leave_requests", sa.Column("leave_type_id", postgresql.UUID(as_uuid=True), nullable=True))
    _add_column("staff_leave_requests", sa.Column("working_days", sa.Numeric(5, 1), nullable=True))
    _add_column(
        "staff_leave_requests", sa.Column("resubmitted_from_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    create_index_if_missing("ix_staff_leave_requests_leave_type_id", "staff_leave_requests", ["leave_type_id"])
    _add_fk(
        "staff_leave_requests",
        "fk_staff_leave_requests_type_same_school",
        "FOREIGN KEY (leave_type_id, school_id) REFERENCES hr_leave_types (id, school_id)",
    )
    _add_fk(
        "staff_leave_requests",
        "fk_staff_leave_requests_resubmitted_from",
        "FOREIGN KEY (resubmitted_from_id) REFERENCES staff_leave_requests (id) ON DELETE SET NULL",
    )
    _add_fk(
        "staff_leave_requests",
        "uq_staff_leave_requests_resubmitted_from_id",
        "UNIQUE (resubmitted_from_id)",
    )

    # ── staff attendance ─────────────────────────────────────────────────────
    _add_column("staff_attendance", sa.Column("check_in", sa.Time(), nullable=True))
    _add_column("staff_attendance", sa.Column("check_out", sa.Time(), nullable=True))
    _add_column("staff_attendance", sa.Column("leave_request_id", postgresql.UUID(as_uuid=True), nullable=True))
    create_index_if_missing("ix_staff_attendance_leave_request_id", "staff_attendance", ["leave_request_id"])
    _add_fk(
        "staff_attendance",
        "fk_staff_attendance_leave_request_id_staff_leave_requests",
        "FOREIGN KEY (leave_request_id) REFERENCES staff_leave_requests (id) ON DELETE SET NULL",
    )
    _add_check(
        "staff_attendance",
        "ck_staff_attendance_check_out_after_in",
        "check_in IS NULL OR check_out IS NULL OR check_out > check_in",
    )

    # ── letters ──────────────────────────────────────────────────────────────
    op.alter_column("hr_letters", "letter_type", type_=sa.String(32), existing_nullable=False)
    _add_column("hr_letters", sa.Column("template_version", sa.Integer(), nullable=True))
    _replace_check("hr_letters", "ck_hr_letters_type_allowed", _in("letter_type", NEW_LETTER_TYPES))

    # ── employee documents ───────────────────────────────────────────────────
    _add_column(
        "employee_documents",
        sa.Column("verification_status", sa.String(16), nullable=False, server_default="PENDING"),
    )
    _add_column("employee_documents", _uuid_fk_users("verified_by"))
    _add_column("employee_documents", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    _add_column("employee_documents", sa.Column("verification_note", sa.Text(), nullable=True))
    _add_column("employee_documents", sa.Column("expiry_date", sa.Date(), nullable=True))
    create_index_if_missing("ix_employee_documents_expiry_date", "employee_documents", ["expiry_date"])
    _add_fk(
        "employee_documents",
        "fk_employee_documents_verified_by_users",
        "FOREIGN KEY (verified_by) REFERENCES users (id) ON DELETE SET NULL",
    )
    _add_check(
        "employee_documents",
        "ck_employee_documents_verification_allowed",
        "verification_status IN ('PENDING', 'VERIFIED', 'REJECTED')",
    )
    _add_check(
        "employee_documents",
        "ck_employee_documents_verified_has_time",
        "verification_status = 'PENDING' OR verified_at IS NOT NULL",
    )
    _replace_check("employee_documents", "ck_employee_documents_category_allowed", _in("category", NEW_CATEGORIES))

    # ── settings ─────────────────────────────────────────────────────────────
    _add_column("hr_settings", sa.Column("employee_code_prefix", sa.String(10), nullable=True))
    _add_column(
        "hr_settings",
        sa.Column(
            "working_weekdays",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[0, 1, 2, 3, 4, 5]'::jsonb"),
        ),
    )
    _add_column("hr_settings", sa.Column("work_start_time", sa.Time(), nullable=True))
    _add_column(
        "hr_settings", sa.Column("late_grace_minutes", sa.Integer(), nullable=False, server_default=sa.text("10"))
    )
    _add_column(
        "hr_settings",
        sa.Column("leave_balance_enforced", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    _add_column(
        "hr_settings", sa.Column("leave_min_notice_days", sa.Integer(), nullable=False, server_default=sa.text("0"))
    )
    _add_column(
        "hr_settings",
        sa.Column("document_expiry_warning_days", sa.Integer(), nullable=False, server_default=sa.text("30")),
    )
    _add_column("hr_settings", sa.Column("onboarding_checklist", postgresql.JSONB(), nullable=True))
    _add_column("hr_settings", sa.Column("offboarding_checklist", postgresql.JSONB(), nullable=True))
    _add_check(
        "hr_settings",
        "ck_hr_settings_code_prefix_format",
        "employee_code_prefix IS NULL OR employee_code_prefix ~ '^[A-Z0-9]{1,10}$'",
    )
    _add_check("hr_settings", "ck_hr_settings_working_weekdays_array", "jsonb_typeof(working_weekdays) = 'array'")
    _add_check("hr_settings", "ck_hr_settings_late_grace_range", "late_grace_minutes BETWEEN 0 AND 240")
    _add_check("hr_settings", "ck_hr_settings_leave_notice_range", "leave_min_notice_days BETWEEN 0 AND 90")
    _add_check(
        "hr_settings", "ck_hr_settings_expiry_warning_range", "document_expiry_warning_days BETWEEN 0 AND 365"
    )
    _secure_new_hr_tables()


def downgrade() -> None:
    for name in (
        "ck_hr_settings_expiry_warning_range",
        "ck_hr_settings_leave_notice_range",
        "ck_hr_settings_late_grace_range",
        "ck_hr_settings_working_weekdays_array",
        "ck_hr_settings_code_prefix_format",
    ):
        op.execute(f"ALTER TABLE hr_settings DROP CONSTRAINT IF EXISTS {name}")
    for col in (
        "offboarding_checklist",
        "onboarding_checklist",
        "document_expiry_warning_days",
        "leave_min_notice_days",
        "leave_balance_enforced",
        "late_grace_minutes",
        "work_start_time",
        "working_weekdays",
        "employee_code_prefix",
    ):
        op.drop_column("hr_settings", col)

    op.execute("UPDATE employee_documents SET category = 'OTHER' WHERE category IN ('TAX', 'DISCIPLINARY')")
    _replace_check("employee_documents", "ck_employee_documents_category_allowed", _in("category", OLD_CATEGORIES))
    op.execute("ALTER TABLE employee_documents DROP CONSTRAINT IF EXISTS ck_employee_documents_verified_has_time")
    op.execute("ALTER TABLE employee_documents DROP CONSTRAINT IF EXISTS ck_employee_documents_verification_allowed")
    op.execute("ALTER TABLE employee_documents DROP CONSTRAINT IF EXISTS fk_employee_documents_verified_by_users")
    op.execute("DROP INDEX IF EXISTS ix_employee_documents_expiry_date")
    for col in ("expiry_date", "verification_note", "verified_at", "verified_by", "verification_status"):
        op.drop_column("employee_documents", col)

    op.execute(f"UPDATE hr_letters SET letter_type = 'OTHER' WHERE NOT ({_in('letter_type', OLD_LETTER_TYPES)})")
    _replace_check("hr_letters", "ck_hr_letters_type_allowed", _in("letter_type", OLD_LETTER_TYPES))
    op.drop_column("hr_letters", "template_version")
    op.alter_column("hr_letters", "letter_type", type_=sa.String(16), existing_nullable=False)

    op.execute("ALTER TABLE staff_attendance DROP CONSTRAINT IF EXISTS ck_staff_attendance_check_out_after_in")
    op.execute(
        "ALTER TABLE staff_attendance DROP CONSTRAINT IF EXISTS "
        "fk_staff_attendance_leave_request_id_staff_leave_requests"
    )
    op.execute("DROP INDEX IF EXISTS ix_staff_attendance_leave_request_id")
    for col in ("leave_request_id", "check_out", "check_in"):
        op.drop_column("staff_attendance", col)

    for name in (
        "uq_staff_leave_requests_resubmitted_from_id",
        "fk_staff_leave_requests_resubmitted_from",
        "fk_staff_leave_requests_type_same_school",
    ):
        op.execute(f"ALTER TABLE staff_leave_requests DROP CONSTRAINT IF EXISTS {name}")
    op.execute("DROP INDEX IF EXISTS ix_staff_leave_requests_leave_type_id")
    for col in ("resubmitted_from_id", "working_days", "leave_type_id"):
        op.drop_column("staff_leave_requests", col)

    op.drop_table("employee_history")
    for name in (
        "ck_employees_exit_reason_allowed",
        "ck_employees_manager_not_self",
        "fk_employees_manager_same_school",
    ):
        op.execute(f"ALTER TABLE employees DROP CONSTRAINT IF EXISTS {name}")
    op.execute("DROP INDEX IF EXISTS ix_employees_reporting_manager_id")
    for col in ("exit_notes", "exit_reason", "exit_date", "reporting_manager_id"):
        op.drop_column("employees", col)

    op.drop_table("hr_letter_templates")
    op.drop_table("hr_holidays")
    op.drop_table("hr_leave_types")
