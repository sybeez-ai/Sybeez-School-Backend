"""Phase 3 hardening: enrollment history + roll uniqueness.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    # Allow multiple enrollment rows per student+year (TRANSFERRED history),
    # but only one ACTIVE placement at a time.
    op.execute("ALTER TABLE enrollments DROP CONSTRAINT IF EXISTS uq_enrollment_student_year")
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_enrollment_student_year_active
            ON enrollments (student_id, academic_year_id)
            WHERE status = 'ACTIVE';
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_enrollment_class_roll_active
            ON enrollments (class_id, roll_number)
            WHERE status = 'ACTIVE' AND roll_number IS NOT NULL AND roll_number <> '';
        EXCEPTION WHEN duplicate_table THEN NULL;
                   WHEN duplicate_object THEN NULL;
        END $$;
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS uq_enrollment_class_roll_active")
    op.execute("DROP INDEX IF EXISTS uq_enrollment_student_year_active")
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE enrollments
            ADD CONSTRAINT uq_enrollment_student_year UNIQUE (student_id, academic_year_id);
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
