"""Phase 3 student master + enrollment roll number.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from typing import Sequence, Union

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS middle_name VARCHAR(100)")

    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS blood_group VARCHAR(16)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS nationality VARCHAR(64)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS address_line1 VARCHAR(255)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS address_line2 VARCHAR(255)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS city VARCHAR(100)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS state VARCHAR(100)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS postal_code VARCHAR(32)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS country VARCHAR(100)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS phone VARCHAR(32)")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS admission_date DATE")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS photo_url TEXT")
    op.execute("ALTER TABLE students ADD COLUMN IF NOT EXISTS student_code VARCHAR(64)")

    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_students_school_admission_no
            ON students (school_id, admission_no)
            WHERE admission_no IS NOT NULL;
        EXCEPTION WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          CREATE UNIQUE INDEX uq_students_school_student_code
            ON students (school_id, student_code)
            WHERE student_code IS NOT NULL;
        EXCEPTION WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
        END $$;
        """
    )

    op.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS roll_number VARCHAR(32)")
    op.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS left_at TIMESTAMPTZ")
    op.execute("ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS notes TEXT")


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE enrollments DROP COLUMN IF EXISTS notes")
    op.execute("ALTER TABLE enrollments DROP COLUMN IF EXISTS left_at")
    op.execute("ALTER TABLE enrollments DROP COLUMN IF EXISTS roll_number")
    op.execute("DROP INDEX IF EXISTS uq_students_school_student_code")
    op.execute("DROP INDEX IF EXISTS uq_students_school_admission_no")
    for col in (
        "student_code",
        "photo_url",
        "admission_date",
        "phone",
        "country",
        "postal_code",
        "state",
        "city",
        "address_line2",
        "address_line1",
        "nationality",
        "blood_group",
    ):
        op.execute(f"ALTER TABLE students DROP COLUMN IF EXISTS {col}")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS middle_name")
