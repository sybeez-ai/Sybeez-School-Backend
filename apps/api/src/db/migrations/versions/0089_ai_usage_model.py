"""Per-model AI token rows, and let a platform bypass write tenant usage and plans.

Revision ID: 0089
Revises: 0088

platform_ai_user_usage was unique on (school_id, user_id) and stored one token
total. Usage is now kept per model. Existing rows become model "unknown".

Plan delete updates every school's subscription, and removing a school admin
updates that membership. Those WITH CHECK clauses ignored app.rls_bypass, so
the write failed even after the session turned bypass on. The same gap blocked
inserts into platform_ai_usage_daily for a school other than the session school.
This revision ORs the bypass flag into those write checks. Reads are unchanged.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "0089"
down_revision: Union[str, None] = "0088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
_WRITE_TABLES = ("subscriptions", "platform_ai_usage_daily", "school_memberships")


def _policies(table: str):
    return op.get_bind().execute(
        text(
            """
            SELECT pol.polname, pol.polpermissive,
                   pg_get_expr(pol.polqual, pol.polrelid),
                   pg_get_expr(pol.polwithcheck, pol.polrelid)
            FROM pg_policy pol
            JOIN pg_class c ON c.oid = pol.polrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = :table
            """
        ),
        {"table": table},
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


def _allow_bypass_writes(table: str) -> None:
    for name, permissive, qual, check in _policies(table):
        if check and "app.rls_bypass" in check:
            continue
        base = check if check else qual
        if not base:
            continue
        if "app.rls_bypass" in base:
            continue
        _recreate(table, name, permissive, qual, f"({_BYPASS}) OR ({base})")


def upgrade() -> None:
    op.add_column(
        "platform_ai_user_usage",
        sa.Column("model", sa.String(length=120), nullable=False, server_default="unknown"),
    )
    op.drop_constraint("uq_ai_user_usage_school_user", "platform_ai_user_usage", type_="unique")
    op.create_unique_constraint(
        "uq_ai_user_usage_school_user_model",
        "platform_ai_user_usage",
        ["school_id", "user_id", "model"],
    )
    op.create_index(
        "uq_ai_usage_daily_no_school",
        "platform_ai_usage_daily",
        ["usage_date", "provider", "model", "capability"],
        unique=True,
        postgresql_where=sa.text("school_id IS NULL"),
    )
    for table in _WRITE_TABLES:
        _allow_bypass_writes(table)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ai_usage_daily_no_school")
    op.execute(
        """
        UPDATE platform_ai_user_usage AS keep
        SET tokens = summed.tokens
        FROM (
            SELECT school_id, user_id, SUM(tokens) AS tokens,
                   (ARRAY_AGG(id ORDER BY created_at ASC, id ASC))[1] AS keep_id
            FROM platform_ai_user_usage
            GROUP BY school_id, user_id
        ) AS summed
        WHERE keep.id = summed.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM platform_ai_user_usage AS extra
        USING (
            SELECT school_id, user_id,
                   (ARRAY_AGG(id ORDER BY created_at ASC, id ASC))[1] AS keep_id
            FROM platform_ai_user_usage
            GROUP BY school_id, user_id
        ) AS summed
        WHERE extra.school_id = summed.school_id
          AND extra.user_id = summed.user_id
          AND extra.id <> summed.keep_id
        """
    )
    op.drop_constraint("uq_ai_user_usage_school_user_model", "platform_ai_user_usage", type_="unique")
    op.create_unique_constraint(
        "uq_ai_user_usage_school_user",
        "platform_ai_user_usage",
        ["school_id", "user_id"],
    )
    op.drop_column("platform_ai_user_usage", "model")
