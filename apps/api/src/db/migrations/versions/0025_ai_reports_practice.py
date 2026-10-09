"""Phase 17: AI parent reports + practice attempts.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-05
"""

from typing import Sequence, Union

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS parent_ai_reports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            parent_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            title VARCHAR(255) NOT NULL,
            summary_json TEXT NOT NULL DEFAULT '{}',
            body_markdown TEXT NOT NULL DEFAULT '',
            status VARCHAR(32) NOT NULL DEFAULT 'READY',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_ai_reports_school_id ON parent_ai_reports (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_ai_reports_parent_user_id ON parent_ai_reports (parent_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_ai_reports_student_id ON parent_ai_reports (student_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_parent_ai_reports_created_at ON parent_ai_reports (created_at DESC)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_attempts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
            student_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subject_id UUID REFERENCES subjects(id) ON DELETE SET NULL,
            topic VARCHAR(255),
            difficulty VARCHAR(32) NOT NULL DEFAULT 'adaptive',
            questions_json TEXT NOT NULL DEFAULT '[]',
            answers_json TEXT NOT NULL DEFAULT '[]',
            score_pct NUMERIC(5,2),
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_attempts_student_user_id ON practice_attempts (student_user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_attempts_school_id ON practice_attempts (school_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_practice_attempts_created_at ON practice_attempts (created_at DESC)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS practice_attempts")
    op.execute("DROP TABLE IF EXISTS parent_ai_reports")
