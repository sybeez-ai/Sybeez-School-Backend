"""Phase 11 analytics settings, saved reports, scheduled reports, audit.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS analytics_settings (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            attendance_threshold DOUBLE PRECISION NOT NULL DEFAULT 75,
            missing_homework_threshold INTEGER NOT NULL DEFAULT 3,
            low_exam_percentage_threshold DOUBLE PRECISION NOT NULL DEFAULT 35,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_analytics_settings_school UNIQUE (school_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_analytics_settings_school_id ON analytics_settings (school_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS saved_reports (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            name VARCHAR(255) NOT NULL,
            report_type VARCHAR(64) NOT NULL,
            configuration_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_saved_reports_school_id ON saved_reports (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_saved_reports_type ON saved_reports (report_type)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_reports (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            report_type VARCHAR(64) NOT NULL,
            configuration_json TEXT NOT NULL DEFAULT '{}',
            schedule VARCHAR(128) NOT NULL DEFAULT 'MONTHLY',
            recipients_json TEXT NOT NULL DEFAULT '[]',
            active BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_reports_school_id ON scheduled_reports (school_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS report_audit_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            report_type VARCHAR(64) NOT NULL,
            filters_json TEXT,
            action VARCHAR(64) NOT NULL,
            entity_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_report_audit_logs_school_id ON report_audit_logs (school_id)"
    )


def downgrade() -> None:
    from alembic import op

    for t in ("report_audit_logs", "scheduled_reports", "saved_reports", "analytics_settings"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
