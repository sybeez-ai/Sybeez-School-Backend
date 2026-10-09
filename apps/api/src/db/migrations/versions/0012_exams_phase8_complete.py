"""Phase 8.1 exam completeness: instructions, created_by, grading active, published_by.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        "ALTER TABLE grading_scales ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "ALTER TABLE exam_subjects ADD COLUMN IF NOT EXISTS instructions TEXT"
    )
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS published_by UUID REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE results ADD COLUMN IF NOT EXISTS published_by UUID REFERENCES users(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE results DROP COLUMN IF EXISTS published_by")
    op.execute("ALTER TABLE exams DROP COLUMN IF EXISTS published_by")
    op.execute("ALTER TABLE exams DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE exam_subjects DROP COLUMN IF EXISTS instructions")
    op.execute("ALTER TABLE grading_scales DROP COLUMN IF EXISTS is_active")
