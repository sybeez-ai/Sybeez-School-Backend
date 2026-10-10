"""HRM Phase 10: per-school HR business settings.

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_settings (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            letter_reference_prefix VARCHAR(10) NOT NULL DEFAULT 'HR',
            required_document_categories JSONB NOT NULL
                DEFAULT '["ID_PROOF", "CONTRACT", "QUALIFICATION", "BANK"]'::jsonb,
            leave_max_days INTEGER NOT NULL DEFAULT 366,
            onboarding_target_days INTEGER NOT NULL DEFAULT 14,
            notify_leave BOOLEAN NOT NULL DEFAULT TRUE,
            notify_requests BOOLEAN NOT NULL DEFAULT TRUE,
            notify_letters BOOLEAN NOT NULL DEFAULT TRUE,
            notify_documents BOOLEAN NOT NULL DEFAULT TRUE,
            notify_onboarding BOOLEAN NOT NULL DEFAULT TRUE,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_hr_settings_school UNIQUE (school_id),
            CONSTRAINT ck_hr_settings_letter_prefix_format
                CHECK (letter_reference_prefix ~ '^[A-Z0-9]{1,10}$'),
            CONSTRAINT ck_hr_settings_leave_max_days_range CHECK (leave_max_days BETWEEN 1 AND 366),
            CONSTRAINT ck_hr_settings_onboarding_target_range
                CHECK (onboarding_target_days BETWEEN 0 AND 365),
            CONSTRAINT ck_hr_settings_required_docs_array
                CHECK (jsonb_typeof(required_document_categories) = 'array')
        )
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS hr_settings")
