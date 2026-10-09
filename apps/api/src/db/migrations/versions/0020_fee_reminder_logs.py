"""Phase 12 fees: reminder logs for invoice due/overdue notifications.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_reminder_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            invoice_id UUID NOT NULL REFERENCES fee_invoices(id) ON DELETE CASCADE,
            kind VARCHAR(64) NOT NULL,
            sent_on DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_reminder_invoice_kind_day UNIQUE (invoice_id, kind, sent_on)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_reminder_logs_school_id ON fee_reminder_logs (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_reminder_logs_invoice_id ON fee_reminder_logs (invoice_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_reminder_logs_kind ON fee_reminder_logs (kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_reminder_logs_sent_on ON fee_reminder_logs (sent_on)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS fee_reminder_logs CASCADE")

