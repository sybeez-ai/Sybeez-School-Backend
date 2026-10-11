"""Additive RLS helpers for platform access. Does not replace payroll or finance policies.

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-11

Renumbered from platform 9002. Release already forces RLS and installs
payroll_is_staff / app.can_see policies. This revision only adds functions that
read the same app.* settings, and turns RLS on for the new permission table
that carries school_id. It does not drop existing policies and does not install
the event trigger that would attach a second policy to later tables.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0048"
down_revision: Union[str, None] = "0047"
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

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_app') THEN
        CREATE ROLE sybeez_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;
"""


def upgrade() -> None:
    op.execute(_FUNCTIONS)
    op.execute("ALTER TABLE membership_permission_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE membership_permission_overrides FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON membership_permission_overrides")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON membership_permission_overrides
        USING (rls_tenant(school_id))
        WITH CHECK (rls_tenant(school_id))
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON membership_permission_overrides TO sybeez_app"
    )
    op.execute("GRANT SELECT ON permissions TO sybeez_app")
    op.execute("GRANT SELECT ON role_permissions TO sybeez_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON membership_permission_overrides")
    op.execute("ALTER TABLE membership_permission_overrides NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE membership_permission_overrides DISABLE ROW LEVEL SECURITY")
    for name in (
        "rls_tenant(uuid)",
        "rls_membership()",
        "rls_school()",
        "rls_uid()",
        "rls_role()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {name}")
