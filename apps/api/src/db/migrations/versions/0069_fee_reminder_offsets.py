"""Per-school fee reminder offsets on school_alert_settings (existing table, existing RLS/grants).

Revision ID: 0069
Revises: 0068
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0069"
down_revision: Union[str, None] = "0068"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE school_alert_settings "
        "ADD COLUMN IF NOT EXISTS fee_reminder_days integer[] NOT NULL DEFAULT '{7,2}'"
    )
    op.execute(
        "ALTER TABLE school_alert_settings "
        "ADD COLUMN IF NOT EXISTS fee_overdue_days integer[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE school_alert_settings DROP COLUMN IF EXISTS fee_overdue_days")
    op.execute("ALTER TABLE school_alert_settings DROP COLUMN IF EXISTS fee_reminder_days")
