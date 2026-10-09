"""Phase 4 teacher master fields.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS teacher_code VARCHAR(64)")
    op.execute("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS phone VARCHAR(32)")
    op.execute("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS joining_date DATE")
    op.execute("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS qualification VARCHAR(255)")
    op.execute("ALTER TABLE teachers ADD COLUMN IF NOT EXISTS photo_url TEXT")
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_teachers_school_teacher_code
            ON teachers (school_id, teacher_code)
            WHERE teacher_code IS NOT NULL;
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_teachers_school_employee_code
            ON teachers (school_id, employee_code)
            WHERE employee_code IS NOT NULL;
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS uq_teachers_school_employee_code")
    op.execute("DROP INDEX IF EXISTS uq_teachers_school_teacher_code")
    op.execute("ALTER TABLE teachers DROP COLUMN IF EXISTS photo_url")
    op.execute("ALTER TABLE teachers DROP COLUMN IF EXISTS qualification")
    op.execute("ALTER TABLE teachers DROP COLUMN IF EXISTS joining_date")
    op.execute("ALTER TABLE teachers DROP COLUMN IF EXISTS phone")
    op.execute("ALTER TABLE teachers DROP COLUMN IF EXISTS teacher_code")
