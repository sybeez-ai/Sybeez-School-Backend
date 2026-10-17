"""Per-student AI learning profiles (written by the AI service), with audience RLS.

The AI service connects as sybeez_app, which cannot create tables, so the owner-run backend
migration owns this table. Audience: the student, linked parents and linked teachers read; only
the student writes. Nothing here stores conversation text.

Revision ID: 0066
Revises: 0065
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0066"
down_revision: Union[str, None] = "0065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
USER = "NULLIF(current_setting('app.user_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS student_learning_profiles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            school_id UUID NOT NULL,
            student_user_id UUID NOT NULL,
            student_id UUID,
            guardian_user_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
            teacher_user_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            reset_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_student_learning_profiles_student "
        "ON student_learning_profiles (school_id, student_user_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_learning_profiles_school ON student_learning_profiles (school_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_student_learning_profiles_guardians "
        "ON student_learning_profiles USING gin (guardian_user_ids)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_student_learning_profiles_teachers "
        "ON student_learning_profiles USING gin (teacher_user_ids)"
    )
    op.execute("ALTER TABLE student_learning_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE student_learning_profiles FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS student_learning_profiles_audience ON student_learning_profiles")
    op.execute(
        f"""
        CREATE POLICY student_learning_profiles_audience ON student_learning_profiles
        USING ({TENANT} AND (student_user_id = {USER}
            OR {USER} = ANY(guardian_user_ids) OR {USER} = ANY(teacher_user_ids)))
        WITH CHECK ({TENANT} AND student_user_id = {USER})
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_app') THEN
            GRANT SELECT, INSERT, UPDATE, DELETE ON student_learning_profiles TO sybeez_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS student_learning_profiles_audience ON student_learning_profiles")
    op.execute("DROP TABLE IF EXISTS student_learning_profiles")
