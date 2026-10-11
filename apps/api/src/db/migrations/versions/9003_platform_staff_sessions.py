"""Platform staff grants, per-role session cap, and staff RLS.

Revision ID: 9003
Revises: 9002
Create Date: 2026-10-11

Range 9000-9099 is reserved for the platform branch.
"""

from __future__ import annotations

import re
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "9003"
down_revision: Union[str, None] = "9002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rewrite_role_checks(expression: str) -> str:
    def repl_any(match: re.Match[str]) -> str:
        roles = re.findall(r"'([A-Z_]+)'", match.group(1))
        return "(" + " OR ".join(f"rls_has('{role}')" for role in roles) + ")"

    updated = re.sub(r"rls_role\(\) = ANY \(ARRAY\[(.*?)\]\)", repl_any, expression, flags=re.DOTALL)
    updated = re.sub(r"rls_role\(\) = '([A-Z_]+)'::text", r"rls_has('\1')", updated)
    updated = re.sub(r"rls_role\(\) = '([A-Z_]+)'", r"rls_has('\1')", updated)
    return updated


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE refresh_tokens
            ADD COLUMN IF NOT EXISTS family_expires_at timestamptz
        """
    )
    op.execute(
        """
        ALTER TABLE refresh_tokens
            ADD COLUMN IF NOT EXISTS absolute_session boolean NOT NULL DEFAULT false
        """
    )
    op.execute(
        """
        UPDATE refresh_tokens
        SET family_expires_at = expires_at
        WHERE family_expires_at IS NULL
        """
    )
    op.execute("ALTER TABLE refresh_tokens ALTER COLUMN family_expires_at SET NOT NULL")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_staff (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
            status varchar(32) NOT NULL DEFAULT 'ACTIVE',
            invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_staff_portals (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id uuid NOT NULL REFERENCES platform_staff(id) ON DELETE CASCADE,
            portal varchar(32) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (staff_id, portal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_staff_permissions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            staff_id uuid NOT NULL REFERENCES platform_staff(id) ON DELETE CASCADE,
            permission_code varchar(64) NOT NULL,
            granted_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (staff_id, permission_code)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_staff_invitations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email varchar(320) NOT NULL,
            token_hash varchar(128) NOT NULL UNIQUE,
            portals jsonb NOT NULL,
            permissions jsonb NOT NULL,
            status varchar(32) NOT NULL,
            invited_by uuid REFERENCES users(id) ON DELETE SET NULL,
            expires_at timestamptz NOT NULL,
            accepted_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_staff_portal(portal text) RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT portal IS NOT NULL AND EXISTS (
            SELECT 1
            FROM unnest(string_to_array(COALESCE(current_setting('app.portals', true), ''), ',')) AS granted
            WHERE btrim(granted) = portal
          )
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_has(role_name text) RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT rls_role() = role_name
             OR (
               rls_role() = 'PLATFORM_STAFF'
               AND (
                    (role_name = 'SCHOOL_ADMIN' AND rls_staff_portal('school_admin'))
                 OR (role_name = 'FINANCE' AND (rls_staff_portal('finance') OR rls_staff_portal('school_admin')))
                 OR (role_name = 'HR' AND (rls_staff_portal('hrm') OR rls_staff_portal('school_admin')))
                 OR (role_name = 'PAYROLL_APPROVER' AND (rls_staff_portal('payroll') OR rls_staff_portal('school_admin')))
               )
             )
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_privileged() RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT rls_role() IN ('PLATFORM_ADMIN', 'SCHOOL_ADMIN')
             OR (rls_role() = 'PLATFORM_STAFF' AND rls_staff_portal('school_admin'))
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_user_row_writable(uid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT rls_role() = 'PLATFORM_ADMIN'
             OR uid = rls_uid()
             OR (
               (
                 rls_role() IN ('SCHOOL_ADMIN', 'HR')
                 OR (rls_role() = 'PLATFORM_STAFF' AND (rls_staff_portal('school_admin') OR rls_staff_portal('hrm')))
               )
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
    )

    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT c.relname, pol.polname, pol.polpermissive,
                   pg_get_expr(pol.polqual, pol.polrelid),
                   pg_get_expr(pol.polwithcheck, pol.polrelid)
            FROM pg_policy pol
            JOIN pg_class c ON c.oid = pol.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND (
                pg_get_expr(pol.polqual, pol.polrelid) LIKE '%rls_role()%'
                OR COALESCE(pg_get_expr(pol.polwithcheck, pol.polrelid), '') LIKE '%rls_role()%'
              )
            """
        )
    ).fetchall()
    for relname, policy, permissive, qual, check in rows:
        new_qual = _rewrite_role_checks(qual or "true")
        new_check = _rewrite_role_checks(check) if check else new_qual
        if new_qual == qual and (check is None or new_check == check):
            continue
        kind = "" if permissive else "AS RESTRICTIVE"
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{relname}"')
        op.execute(
            f'CREATE POLICY "{policy}" ON "{relname}" {kind} USING ({new_qual}) WITH CHECK ({new_check})'
        )

    for table, using, check in (
        (
            "platform_staff",
            "rls_role() = 'PLATFORM_ADMIN' OR user_id = rls_uid()",
            "rls_role() = 'PLATFORM_ADMIN'",
        ),
        (
            "platform_staff_portals",
            """
            rls_role() = 'PLATFORM_ADMIN'
            OR EXISTS (
                SELECT 1 FROM platform_staff s
                WHERE s.id = platform_staff_portals.staff_id AND s.user_id = rls_uid()
            )
            """,
            "rls_role() = 'PLATFORM_ADMIN'",
        ),
        (
            "platform_staff_permissions",
            """
            rls_role() = 'PLATFORM_ADMIN'
            OR EXISTS (
                SELECT 1 FROM platform_staff s
                WHERE s.id = platform_staff_permissions.staff_id AND s.user_id = rls_uid()
            )
            """,
            "rls_role() = 'PLATFORM_ADMIN'",
        ),
        (
            "platform_staff_invitations",
            "rls_role() = 'PLATFORM_ADMIN'",
            "rls_role() = 'PLATFORM_ADMIN'",
        ),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} USING ({using}) WITH CHECK ({check})"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_staff TO sybeez_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_staff_portals TO sybeez_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_staff_permissions TO sybeez_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_staff_invitations TO sybeez_app")


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_has(role_name text) RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT rls_role() = role_name
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION rls_privileged() RETURNS boolean
        LANGUAGE sql STABLE AS $$
          SELECT rls_role() IN ('PLATFORM_ADMIN', 'SCHOOL_ADMIN')
        $$;
        """
    )
    op.execute("DROP TABLE IF EXISTS platform_staff_invitations")
    op.execute("DROP TABLE IF EXISTS platform_staff_permissions")
    op.execute("DROP TABLE IF EXISTS platform_staff_portals")
    op.execute("DROP TABLE IF EXISTS platform_staff")
    op.execute("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS absolute_session")
    op.execute("ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS family_expires_at")
    op.execute("DROP FUNCTION IF EXISTS rls_staff_portal(text)")
