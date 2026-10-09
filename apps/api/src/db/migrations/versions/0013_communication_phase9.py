"""Phase 9 communication: notifications, announcements, messaging, preferences.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    # notifications extensions
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS entity_type VARCHAR(64)")
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS entity_id UUID")
    op.execute(
        "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS priority VARCHAR(16) NOT NULL DEFAULT 'NORMAL'"
    )
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS event_key VARCHAR(255)")
    op.execute("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_category ON notifications (category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_is_read ON notifications (is_read)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_notifications_event_key ON notifications (event_key)")
    op.execute(
        """
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_notification_event_user'
            ) THEN
                ALTER TABLE notifications
                  ADD CONSTRAINT uq_notification_event_user UNIQUE (event_key, user_id);
            END IF;
        END $$;
        """
    )

    # announcement extensions
    op.execute(
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'DRAFT'"
    )
    op.execute(
        "ALTER TABLE announcements ADD COLUMN IF NOT EXISTS priority VARCHAR(16) NOT NULL DEFAULT 'NORMAL'"
    )
    op.execute("ALTER TABLE announcements ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ")
    op.execute("ALTER TABLE announcements ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ")
    op.execute(
        "UPDATE announcements SET status = 'PUBLISHED', published_at = COALESCE(published_at, created_at) WHERE is_published = TRUE"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_announcements_status ON announcements (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS announcement_audiences (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            announcement_id UUID NOT NULL REFERENCES announcements(id) ON DELETE CASCADE,
            audience_type VARCHAR(32) NOT NULL,
            role VARCHAR(32),
            grade_id UUID REFERENCES grades(id) ON DELETE CASCADE,
            section_id UUID REFERENCES classes(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_announcement_audiences_school_id ON announcement_audiences (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_announcement_audiences_announcement_id ON announcement_audiences (announcement_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id UUID PRIMARY KEY,
            school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            notification_type VARCHAR(64) NOT NULL,
            in_app_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            email_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            sms_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_notif_pref_user_type UNIQUE (user_id, notification_type)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_notification_preferences_user_id ON notification_preferences (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            conversation_type VARCHAR(32) NOT NULL DEFAULT 'DIRECT',
            subject VARCHAR(255),
            related_student_id UUID REFERENCES students(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_conversations_school_id ON conversations (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_participants (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_read_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_conversation_participant UNIQUE (conversation_id, user_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_participants_user_id ON conversation_participants (user_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            sender_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            message TEXT NOT NULL,
            message_type VARCHAR(32) NOT NULL DEFAULT 'TEXT',
            edited_at TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_conversation_messages_conversation_id ON conversation_messages (conversation_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS communication_audit_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            action VARCHAR(64) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id UUID,
            meta_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_communication_audit_logs_school_id ON communication_audit_logs (school_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS communication_audit_logs CASCADE")
    op.execute("DROP TABLE IF EXISTS conversation_messages CASCADE")
    op.execute("DROP TABLE IF EXISTS conversation_participants CASCADE")
    op.execute("DROP TABLE IF EXISTS conversations CASCADE")
    op.execute("DROP TABLE IF EXISTS notification_preferences CASCADE")
    op.execute("DROP TABLE IF EXISTS announcement_audiences CASCADE")
