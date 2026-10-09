"""Academic structure: grade is_active + class teacher.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from typing import Sequence, Union

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE grades ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "ALTER TABLE classes ADD COLUMN IF NOT EXISTS class_teacher_id UUID"
    )
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE classes
            ADD CONSTRAINT fk_classes_class_teacher
            FOREIGN KEY (class_teacher_id) REFERENCES teachers(id) ON DELETE SET NULL;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_classes_class_teacher_id ON classes (class_teacher_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE classes DROP CONSTRAINT IF EXISTS fk_classes_class_teacher")
    op.execute("DROP INDEX IF EXISTS ix_classes_class_teacher_id")
    op.execute("ALTER TABLE classes DROP COLUMN IF EXISTS class_teacher_id")
    op.execute("ALTER TABLE grades DROP COLUMN IF EXISTS is_active")
