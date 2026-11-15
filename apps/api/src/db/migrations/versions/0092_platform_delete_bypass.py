"""Let a platform bypass write every tenant table, including audit and memberships.

Revision ID: 0092
Revises: 0091

School delete and admin removal run as a platform session. Some policies match
only app.school_id, and their WITH CHECK clause ignored app.rls_bypass, so the
write was rejected even after the session turned bypass on. Membership updates
and audit rows for a school were the ones Silver hit. This ORs the bypass flag
into every existing policy that does not already mention it.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import text

revision: str = "0092"
down_revision: Union[str, None] = "0091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BYPASS = "current_setting('app.rls_bypass', true) = 'on'"


def _policies():
    return op.get_bind().execute(
        text(
            """
            SELECT c.relname, pol.polname, pol.polpermissive,
                   pg_get_expr(pol.polqual, pol.polrelid),
                   pg_get_expr(pol.polwithcheck, pol.polrelid)
            FROM pg_policy pol
            JOIN pg_class c ON c.oid = pol.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
            """
        )
    ).fetchall()


def _is_permissive(value) -> bool:
    if isinstance(value, str):
        return value.lower() in {"t", "true", "1"}
    return bool(value)


def _recreate(table: str, name: str, permissive, qual: str | None, check: str | None) -> None:
    kind = "" if _is_permissive(permissive) else "AS RESTRICTIVE"
    using = f"USING ({qual})" if qual else "USING (true)"
    with_check = f"WITH CHECK ({check})" if check else ""
    op.execute(f'DROP POLICY IF EXISTS "{name}" ON "{table}"')
    op.execute(f'CREATE POLICY "{name}" ON "{table}" {kind} {using} {with_check}')


def upgrade() -> None:
    for table, name, permissive, qual, check in _policies():
        new_qual = qual
        new_check = check
        changed = False
        if qual and "app.rls_bypass" not in qual:
            new_qual = f"({_BYPASS}) OR ({qual})"
            changed = True
        if check and "app.rls_bypass" not in check:
            new_check = f"({_BYPASS}) OR ({check})"
            changed = True
        if changed:
            _recreate(table, name, permissive, new_qual, new_check)


def downgrade() -> None:
    # Widening only. Rebuilding the chain restores the narrower policies.
    pass
