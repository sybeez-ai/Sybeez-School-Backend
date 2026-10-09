"""Platform SaaS control plane — plans, settings, AI config, school profile fields.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    op.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS postal_code VARCHAR(32)")
    op.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS website TEXT")
    op.execute("ALTER TABLE schools ADD COLUMN IF NOT EXISTS currency VARCHAR(8) DEFAULT 'INR'")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_plans (
            id UUID PRIMARY KEY,
            code VARCHAR(64) NOT NULL,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            price_monthly NUMERIC(12, 2) NOT NULL DEFAULT 0,
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            billing_cycle VARCHAR(32) NOT NULL DEFAULT 'monthly',
            max_students INTEGER,
            max_teachers INTEGER,
            max_storage_gb INTEGER,
            ai_monthly_tokens INTEGER,
            entitlements JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_public BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_platform_plans_code UNIQUE (code)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_platform_plans_code ON platform_plans (code)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_settings (
            id UUID PRIMARY KEY,
            platform_name VARCHAR(120) NOT NULL DEFAULT 'Sybeez',
            support_email VARCHAR(320),
            support_phone VARCHAR(32),
            default_country VARCHAR(100),
            default_timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Kolkata',
            default_currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            maintenance_mode BOOLEAN NOT NULL DEFAULT FALSE,
            default_trial_days INTEGER NOT NULL DEFAULT 30,
            invite_emails_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            session_timeout_minutes INTEGER NOT NULL DEFAULT 480,
            max_login_attempts INTEGER NOT NULL DEFAULT 10,
            email_sender_name VARCHAR(120),
            email_sender_address VARCHAR(320),
            storage_max_file_mb INTEGER NOT NULL DEFAULT 25,
            extras JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_ai_settings (
            id UUID PRIMARY KEY,
            default_provider VARCHAR(64) NOT NULL DEFAULT 'qwen',
            fallback_provider VARCHAR(64),
            default_chat_model VARCHAR(120),
            fallback_chat_model VARCHAR(120),
            request_timeout_seconds INTEGER NOT NULL DEFAULT 60,
            max_retries INTEGER NOT NULL DEFAULT 2,
            max_input_tokens INTEGER NOT NULL DEFAULT 8000,
            max_output_tokens INTEGER NOT NULL DEFAULT 2048,
            ai_globally_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            providers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            models_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_ai_usage_daily (
            id UUID PRIMARY KEY,
            usage_date TIMESTAMPTZ NOT NULL,
            school_id UUID,
            provider VARCHAR(64) NOT NULL DEFAULT 'unknown',
            model VARCHAR(120) NOT NULL DEFAULT 'unknown',
            capability VARCHAR(64) NOT NULL DEFAULT 'CHAT',
            role VARCHAR(32),
            requests INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost NUMERIC(12, 6) NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            safety_blocks INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_ai_usage_daily UNIQUE (usage_date, school_id, provider, model, capability)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_ai_usage_daily_usage_date ON platform_ai_usage_daily (usage_date)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_platform_ai_usage_daily_school_id ON platform_ai_usage_daily (school_id)"
    )

    # Seed default plans (idempotent)
    op.execute(
        """
        INSERT INTO platform_plans (
            id, code, name, description, price_monthly, currency, billing_cycle,
            max_students, max_teachers, max_storage_gb, ai_monthly_tokens,
            entitlements, is_active, is_public, sort_order
        )
        SELECT
            gen_random_uuid(), 'starter', 'Starter', 'For small schools',
            0, 'INR', 'monthly', 300, 30, 20, 500000,
            '{"ai.enabled": true, "fees.enabled": true, "analytics.enabled": false, "voice_ai.enabled": false, "advanced_reports.enabled": false}'::jsonb,
            TRUE, TRUE, 1
        WHERE NOT EXISTS (SELECT 1 FROM platform_plans WHERE code = 'starter')
        """
    )
    op.execute(
        """
        INSERT INTO platform_plans (
            id, code, name, description, price_monthly, currency, billing_cycle,
            max_students, max_teachers, max_storage_gb, ai_monthly_tokens,
            entitlements, is_active, is_public, sort_order
        )
        SELECT
            gen_random_uuid(), 'growth', 'Growth', 'Most schools',
            0, 'INR', 'monthly', 1200, 100, 100, 2000000,
            '{"ai.enabled": true, "fees.enabled": true, "analytics.enabled": true, "voice_ai.enabled": true, "advanced_reports.enabled": true}'::jsonb,
            TRUE, TRUE, 2
        WHERE NOT EXISTS (SELECT 1 FROM platform_plans WHERE code = 'growth')
        """
    )
    op.execute(
        """
        INSERT INTO platform_plans (
            id, code, name, description, price_monthly, currency, billing_cycle,
            max_students, max_teachers, max_storage_gb, ai_monthly_tokens,
            entitlements, is_active, is_public, sort_order
        )
        SELECT
            gen_random_uuid(), 'campus', 'Campus', 'Multi-campus groups',
            0, 'INR', 'monthly', NULL, NULL, 500, 10000000,
            '{"ai.enabled": true, "fees.enabled": true, "analytics.enabled": true, "voice_ai.enabled": true, "advanced_reports.enabled": true}'::jsonb,
            TRUE, TRUE, 3
        WHERE NOT EXISTS (SELECT 1 FROM platform_plans WHERE code = 'campus')
        """
    )
    op.execute(
        """
        INSERT INTO platform_plans (
            id, code, name, description, price_monthly, currency, billing_cycle,
            max_students, max_teachers, max_storage_gb, ai_monthly_tokens,
            entitlements, is_active, is_public, sort_order
        )
        SELECT
            gen_random_uuid(), 'enterprise', 'Enterprise', 'Custom enterprise',
            0, 'INR', 'monthly', NULL, NULL, NULL, NULL,
            '{"ai.enabled": true, "fees.enabled": true, "analytics.enabled": true, "voice_ai.enabled": true, "advanced_reports.enabled": true}'::jsonb,
            TRUE, TRUE, 4
        WHERE NOT EXISTS (SELECT 1 FROM platform_plans WHERE code = 'enterprise')
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS platform_ai_usage_daily")
    op.execute("DROP TABLE IF EXISTS platform_ai_settings")
    op.execute("DROP TABLE IF EXISTS platform_settings")
    op.execute("DROP TABLE IF EXISTS platform_plans")
    op.execute("ALTER TABLE schools DROP COLUMN IF EXISTS currency")
    op.execute("ALTER TABLE schools DROP COLUMN IF EXISTS website")
    op.execute("ALTER TABLE schools DROP COLUMN IF EXISTS postal_code")
    op.execute("ALTER TABLE schools DROP COLUMN IF EXISTS state")
