"""Phase 7 homework attachments and fields.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE homework ADD COLUMN IF NOT EXISTS academic_year_id UUID REFERENCES academic_years(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE homework ADD COLUMN IF NOT EXISTS homework_type VARCHAR(32) NOT NULL DEFAULT 'HOMEWORK'"
    )
    op.execute("ALTER TABLE homework ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ")
    op.execute("ALTER TABLE homework ADD COLUMN IF NOT EXISTS max_marks DOUBLE PRECISION")
    op.execute(
        "ALTER TABLE homework ADD COLUMN IF NOT EXISTS allow_resubmission BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_homework_academic_year_id ON homework (academic_year_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_homework_subject_id ON homework (subject_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_homework_teacher_id ON homework (teacher_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_homework_due_at ON homework (due_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_homework_status ON homework (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS homework_attachments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            homework_id UUID NOT NULL REFERENCES homework(id) ON DELETE CASCADE,
            file_name VARCHAR(255) NOT NULL,
            file_key VARCHAR(512) NOT NULL,
            mime_type VARCHAR(128),
            file_size INTEGER,
            uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_homework_attachments_homework_id ON homework_attachments (homework_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_homework_attachments_school_id ON homework_attachments (school_id)"
    )

    op.execute(
        "ALTER TABLE homework_submissions ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE homework_submissions ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_homework_submissions_status ON homework_submissions (status)"
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_homework_submission_student
            ON homework_submissions (homework_id, student_id);
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS submission_attachments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            submission_id UUID NOT NULL REFERENCES homework_submissions(id) ON DELETE CASCADE,
            file_name VARCHAR(255) NOT NULL,
            file_key VARCHAR(512) NOT NULL,
            mime_type VARCHAR(128),
            file_size INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_submission_attachments_submission_id ON submission_attachments (submission_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_submission_attachments_school_id ON submission_attachments (school_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS submission_attachments")
    op.execute("DROP INDEX IF EXISTS uq_homework_submission_student")
    op.execute("DROP INDEX IF EXISTS ix_homework_submissions_status")
    op.execute("ALTER TABLE homework_submissions DROP COLUMN IF EXISTS reviewed_at")
    op.execute("ALTER TABLE homework_submissions DROP COLUMN IF EXISTS reviewed_by")
    op.execute("DROP TABLE IF EXISTS homework_attachments")
    op.execute("DROP INDEX IF EXISTS ix_homework_status")
    op.execute("DROP INDEX IF EXISTS ix_homework_due_at")
    op.execute("DROP INDEX IF EXISTS ix_homework_teacher_id")
    op.execute("DROP INDEX IF EXISTS ix_homework_subject_id")
    op.execute("DROP INDEX IF EXISTS ix_homework_academic_year_id")
    op.execute("ALTER TABLE homework DROP COLUMN IF EXISTS allow_resubmission")
    op.execute("ALTER TABLE homework DROP COLUMN IF EXISTS max_marks")
    op.execute("ALTER TABLE homework DROP COLUMN IF EXISTS assigned_at")
    op.execute("ALTER TABLE homework DROP COLUMN IF EXISTS homework_type")
    op.execute("ALTER TABLE homework DROP COLUMN IF EXISTS academic_year_id")
