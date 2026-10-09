"""Phase 14 attendance: digest logs for daily absence digests.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance_digest_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind VARCHAR(64) NOT NULL,
            sent_on DATE NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_att_digest_user_kind_day UNIQUE (user_id, kind, sent_on)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_digest_logs_school_id ON attendance_digest_logs (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_digest_logs_user_id ON attendance_digest_logs (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_digest_logs_kind ON attendance_digest_logs (kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_attendance_digest_logs_sent_on ON attendance_digest_logs (sent_on)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS attendance_digest_logs CASCADE")

