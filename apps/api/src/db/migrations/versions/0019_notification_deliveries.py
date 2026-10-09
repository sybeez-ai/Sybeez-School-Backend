"""Phase 12 outbound notification deliveries (SMS/WhatsApp) foundation.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    # preferences: add WhatsApp toggle
    op.execute(
        "ALTER TABLE notification_preferences ADD COLUMN IF NOT EXISTS whatsapp_enabled BOOLEAN NOT NULL DEFAULT FALSE"
    )

    # outbound delivery log
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_deliveries (
            id UUID PRIMARY KEY,
            school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            channel VARCHAR(32) NOT NULL,
            notification_type VARCHAR(64),
            "to" VARCHAR(128) NOT NULL,
            title VARCHAR(255),
            body TEXT NOT NULL,
            meta_json TEXT,
            provider VARCHAR(32) NOT NULL DEFAULT 'mock',
            provider_message_id VARCHAR(128),
            status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_attempt_at TIMESTAMPTZ,
            sent_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_school_id ON notification_deliveries (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_user_id ON notification_deliveries (user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_status ON notification_deliveries (status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_channel ON notification_deliveries (channel)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_to ON notification_deliveries (\"to\")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_notification_type ON notification_deliveries (notification_type)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS notification_deliveries CASCADE")
    op.execute("ALTER TABLE notification_preferences DROP COLUMN IF EXISTS whatsapp_enabled")

