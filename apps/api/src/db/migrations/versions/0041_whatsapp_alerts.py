"""WhatsApp template registry, school alert settings, inbound opt-out, and delivery status.

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-11
"""

from typing import Sequence, Union
from uuid import uuid4

from alembic import op
from sqlalchemy import text

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLES = (
    "alert_message_templates",
    "school_alert_settings",
    "alert_inbound_messages",
    "alert_channel_opt_outs",
    "alert_audit_logs",
)


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    for statement in _DDL:
        op.execute(statement)
    _seed_templates()
    op.execute(
        """
        INSERT INTO school_alert_settings (
            school_id, whatsapp_enabled, sms_enabled, quiet_hours_start, quiet_hours_end,
            timezone, default_locale
        )
        SELECT id, FALSE, FALSE, TIME '21:00', TIME '07:00', 'Asia/Kolkata', 'en'
        FROM schools s
        WHERE NOT EXISTS (
            SELECT 1 FROM school_alert_settings a WHERE a.school_id = s.id
        )
        """
    )
    _policies()


def downgrade() -> None:
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for column in (
        "message_mode",
        "dedupe_key",
        "scheduled_for",
        "fallback_of_id",
        "failed_at",
        "read_at",
        "delivered_at",
        "error_code",
        "variables_json",
        "language_code",
        "template_name",
        "locale",
        "event_key",
    ):
        op.execute(f"ALTER TABLE notification_deliveries DROP COLUMN IF EXISTS {column}")
    op.execute("DROP INDEX IF EXISTS ix_notification_deliveries_provider_message_id")
    op.execute("DROP INDEX IF EXISTS uq_notification_deliveries_dedupe")


def _seed_templates() -> None:
    from src.modules.alerts.catalog import DEFAULT_TEMPLATES

    conn = op.get_bind()
    statement = text(
        """
        INSERT INTO alert_message_templates (
            id, school_id, event_key, locale, whatsapp_template_name, language_code,
            variables, sms_dlt_template_id, sms_text, body_text, category, active
        )
        SELECT CAST(:id AS uuid), NULL,
               CAST(:event_key AS varchar), CAST(:locale AS varchar),
               CAST(:name AS varchar), CAST(:language_code AS varchar),
               CAST(:variables AS jsonb), CAST(:dlt AS varchar),
               CAST(:sms_text AS text), CAST(:body_text AS text),
               CAST(:category AS varchar), TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM alert_message_templates
            WHERE school_id IS NULL
              AND event_key = CAST(:event_key_match AS varchar)
              AND locale = CAST(:locale_match AS varchar)
        )
        """
    )
    import json

    for seed in DEFAULT_TEMPLATES:
        conn.execute(
            statement,
            {
                "id": str(uuid4()),
                "event_key": seed.event_key,
                "event_key_match": seed.event_key,
                "locale": seed.locale,
                "locale_match": seed.locale,
                "name": seed.whatsapp_template_name,
                "language_code": seed.language_code,
                "variables": json.dumps(list(seed.variables)),
                "dlt": seed.sms_dlt_template_id,
                "sms_text": seed.sms_text,
                "body_text": seed.body_text,
                "category": seed.category,
            },
        )


def _policies() -> None:
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
    op.execute(
        """
        CREATE POLICY alert_message_templates_tenant ON alert_message_templates
        USING (
            current_setting('app.rls_bypass', true) = 'on'
            OR school_id IS NULL
            OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
            current_setting('app.rls_bypass', true) = 'on'
            OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        """
    )
    for table in ("school_alert_settings", "alert_inbound_messages", "alert_audit_logs"):
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (
                current_setting('app.rls_bypass', true) = 'on'
                OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('app.rls_bypass', true) = 'on'
                OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            """
        )
    op.execute(
        """
        CREATE POLICY alert_channel_opt_outs_tenant ON alert_channel_opt_outs
        USING (
            current_setting('app.rls_bypass', true) = 'on'
            OR school_id IS NULL
            OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
            current_setting('app.rls_bypass', true) = 'on'
            OR school_id IS NULL
            OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        """
    )


_DDL = (
    """
    CREATE TABLE IF NOT EXISTS alert_message_templates (
        id UUID PRIMARY KEY,
        school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
        event_key VARCHAR(64) NOT NULL,
        locale VARCHAR(8) NOT NULL,
        whatsapp_template_name VARCHAR(128) NOT NULL,
        language_code VARCHAR(16) NOT NULL,
        variables JSONB NOT NULL,
        sms_dlt_template_id VARCHAR(64) NOT NULL DEFAULT '',
        sms_text TEXT NOT NULL,
        body_text TEXT NOT NULL,
        category VARCHAR(32) NOT NULL DEFAULT 'UTILITY',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_alert_message_templates_school_id ON alert_message_templates (school_id)",
    "CREATE INDEX IF NOT EXISTS ix_alert_message_templates_event_key ON alert_message_templates (event_key)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_tpl_school
    ON alert_message_templates (school_id, event_key, locale)
    WHERE school_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_tpl_default
    ON alert_message_templates (event_key, locale)
    WHERE school_id IS NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS school_alert_settings (
        school_id UUID PRIMARY KEY REFERENCES schools(id) ON DELETE CASCADE,
        whatsapp_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        sms_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        quiet_hours_start TIME NOT NULL DEFAULT TIME '21:00',
        quiet_hours_end TIME NOT NULL DEFAULT TIME '07:00',
        timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata',
        default_locale VARCHAR(8) NOT NULL DEFAULT 'en',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_inbound_messages (
        id UUID PRIMARY KEY,
        school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
        channel VARCHAR(32) NOT NULL,
        from_phone VARCHAR(32) NOT NULL,
        provider_message_id VARCHAR(128),
        body TEXT NOT NULL,
        raw_json TEXT,
        opt_out BOOLEAN NOT NULL DEFAULT FALSE,
        received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_alert_inbound_messages_school_id ON alert_inbound_messages (school_id)",
    "CREATE INDEX IF NOT EXISTS ix_alert_inbound_messages_from_phone ON alert_inbound_messages (from_phone)",
    """
    CREATE TABLE IF NOT EXISTS alert_channel_opt_outs (
        id UUID PRIMARY KEY,
        school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        phone VARCHAR(32) NOT NULL,
        channel VARCHAR(32) NOT NULL,
        source VARCHAR(32) NOT NULL DEFAULT 'STOP',
        opted_out_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_alert_channel_opt_outs_school_id ON alert_channel_opt_outs (school_id)",
    "CREATE INDEX IF NOT EXISTS ix_alert_channel_opt_outs_phone ON alert_channel_opt_outs (phone)",
    "CREATE INDEX IF NOT EXISTS ix_alert_channel_opt_outs_user_id ON alert_channel_opt_outs (user_id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_opt_out_school
    ON alert_channel_opt_outs (school_id, channel, phone)
    WHERE school_id IS NOT NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_alert_opt_out_global
    ON alert_channel_opt_outs (channel, phone)
    WHERE school_id IS NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS alert_audit_logs (
        id UUID PRIMARY KEY,
        school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
        actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        action VARCHAR(64) NOT NULL,
        entity_type VARCHAR(64) NOT NULL,
        entity_id UUID,
        meta_json TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_alert_audit_logs_school_id ON alert_audit_logs (school_id)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS event_key VARCHAR(64)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS locale VARCHAR(8)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS template_name VARCHAR(128)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS language_code VARCHAR(16)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS variables_json TEXT",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS error_code VARCHAR(64)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS read_at TIMESTAMPTZ",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS fallback_of_id UUID REFERENCES notification_deliveries(id) ON DELETE SET NULL",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS dedupe_key VARCHAR(255)",
    "ALTER TABLE notification_deliveries ADD COLUMN IF NOT EXISTS message_mode VARCHAR(16) NOT NULL DEFAULT 'text'",
    "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_event_key ON notification_deliveries (event_key)",
    "CREATE INDEX IF NOT EXISTS ix_notification_deliveries_provider_message_id ON notification_deliveries (provider_message_id)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_notification_deliveries_dedupe
    ON notification_deliveries (school_id, channel, dedupe_key)
    WHERE dedupe_key IS NOT NULL
    """,
)
