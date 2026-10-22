"""platform_notification_reads: read state and preferences for the platform console bell.

Revision ID: 0081
Revises: 0080
Create Date: 2026-10-19

Platform notifications are derived from audit_logs; this table only stores, per platform
user, what they have read and which notification types they want. Rows are private to the
user (RLS: own user_id; the read bypass for platform handlers).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0081"
down_revision: Union[str, None] = "0080"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "platform_notification_reads"
OWN = "user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import platform_notification_read as models

    del models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[TABLE]], checkfirst=True)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO sybeez_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_own ON {TABLE}")
    op.execute(f"CREATE POLICY {TABLE}_own ON {TABLE} USING ({OWN} OR {BYPASS}) WITH CHECK ({OWN})")


def downgrade() -> None:
    # Keep stored read state on downgrade (no data is ever dropped).
    pass
