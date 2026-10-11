"""Allow platform-level audit rows (school_id IS NULL) for platform admins.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-11

audit_logs policies (0042: audit_logs_school plus the restrictive audit_logs_role, whose
app.can_see() is false without a school) only admitted rows for the session's school.
Every platform action that audits without a school (platform staff grants, team invites,
plan edits) therefore failed with a row-level security violation, surfaced in the console
as 403 "You cannot access or change rows outside your school...".

Each existing audit_logs policy is widened with one extra branch: a row with no school,
written or read by a PLATFORM_ADMIN or SERVICE session. School rows are unchanged.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0061"
down_revision: Union[str, None] = "0060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MARK = "platform_audit_row"
PLATFORM_NULL = (
    "(school_id IS NULL AND current_setting('app.role', true) IN ('PLATFORM_ADMIN', 'SERVICE')"
    f" AND '{MARK}' IS NOT NULL)"
)


def _policies():
    return op.get_bind().execute(
        text(
            """
            SELECT pol.polname, pol.polpermissive,
                   pg_get_expr(pol.polqual, pol.polrelid),
                   pg_get_expr(pol.polwithcheck, pol.polrelid)
            FROM pg_policy pol
            JOIN pg_class c ON c.oid = pol.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = 'audit_logs'
            """
        )
    ).fetchall()


def _recreate(name: str, permissive: bool, qual: str | None, check: str | None) -> None:
    kind = "" if permissive else "AS RESTRICTIVE"
    using = f"USING ({qual})" if qual else ""
    with_check = f"WITH CHECK ({check})" if check else ""
    op.execute(f'DROP POLICY IF EXISTS "{name}" ON audit_logs')
    op.execute(f'CREATE POLICY "{name}" ON audit_logs {kind} {using} {with_check}')


def upgrade() -> None:
    for name, permissive, qual, check in _policies():
        if MARK in (qual or "") or MARK in (check or ""):
            continue
        new_qual = f"({qual}) OR {PLATFORM_NULL}" if qual else None
        new_check = f"({check}) OR {PLATFORM_NULL}" if check else None
        _recreate(name, permissive, new_qual, new_check)


def downgrade() -> None:
    # Widening only; 0042 recreates the narrower policies if the chain is rebuilt.
    pass
