"""School trial-day override and per-person AI token totals.

Revision ID: 0088
Revises: 0087

Both changes are additive. Existing schools keep a null override and continue
to use the platform default. Existing usage rows are not rewritten.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: Union[str, None] = "0087"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("trial_days", sa.Integer(), nullable=True))
    op.create_table(
        "platform_ai_user_usage",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("school_id", sa.Uuid(), sa.ForeignKey("schools.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("school_id", "user_id", name="uq_ai_user_usage_school_user"),
    )
    op.create_index("ix_platform_ai_user_usage_school_id", "platform_ai_user_usage", ["school_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON platform_ai_user_usage TO sybeez_app")
    op.execute("ALTER TABLE platform_ai_user_usage ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE platform_ai_user_usage FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY platform_ai_user_usage_read ON platform_ai_user_usage
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
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS platform_ai_user_usage_read ON platform_ai_user_usage")
    op.drop_index("ix_platform_ai_user_usage_school_id", table_name="platform_ai_user_usage")
    op.drop_table("platform_ai_user_usage")
    op.drop_column("schools", "trial_days")
