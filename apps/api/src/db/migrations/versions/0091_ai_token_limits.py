"""Monthly AI token limits and assigned models.

Revision ID: 0091
Revises: 0090

A school, a role group, or one person can have a monthly token cap and a model.
Person totals for the current month are stored separately from the lifetime rows.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0091"
down_revision: Union[str, None] = "0090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICY = """
USING (
  current_setting('app.rls_bypass', true) = 'on'
  OR NULLIF(current_setting('app.role', true), '') IN ('PLATFORM_ADMIN', 'SERVICE')
  OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
)
WITH CHECK (
  current_setting('app.rls_bypass', true) = 'on'
  OR NULLIF(current_setting('app.role', true), '') IN ('PLATFORM_ADMIN', 'SERVICE')
  OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
)
"""


def _protect(table: str, policy: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {policy} ON {table} {_POLICY}")


def upgrade() -> None:
    op.create_table(
        "platform_ai_limits",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("monthly_token_limit", sa.BigInteger(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "scope IN ('school', 'students', 'staff', 'parents', 'user')",
            name="ck_ai_limits_scope",
        ),
    )
    op.create_index(
        "uq_ai_limit_scope",
        "platform_ai_limits",
        ["school_id", "scope"],
        unique=True,
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_index(
        "uq_ai_limit_user",
        "platform_ai_limits",
        ["school_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_table(
        "platform_ai_user_month",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("month_start", sa.Date(), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("school_id", "user_id", "month_start", name="uq_ai_user_month"),
    )
    op.create_index("ix_platform_ai_user_month_school_id", "platform_ai_user_month", ["school_id"])
    _protect("platform_ai_limits", "platform_ai_limits_read")
    _protect("platform_ai_user_month", "platform_ai_user_month_read")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS platform_ai_user_month_read ON platform_ai_user_month")
    op.execute("DROP POLICY IF EXISTS platform_ai_limits_read ON platform_ai_limits")
    op.drop_index("ix_platform_ai_user_month_school_id", table_name="platform_ai_user_month")
    op.drop_table("platform_ai_user_month")
    op.drop_index("uq_ai_limit_user", table_name="platform_ai_limits")
    op.drop_index("uq_ai_limit_scope", table_name="platform_ai_limits")
    op.drop_table("platform_ai_limits")
