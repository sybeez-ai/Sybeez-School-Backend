"""Student Life Diary — private journal entries.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-28
"""

from typing import Sequence, Union

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS diary_entries (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            entry_date DATE NOT NULL,
            title VARCHAR(200) NOT NULL,
            body TEXT NOT NULL,
            mood VARCHAR(32) NOT NULL DEFAULT 'good',
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_diary_entries_school_id ON diary_entries (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_diary_entries_student_id ON diary_entries (student_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_diary_entries_entry_date ON diary_entries (entry_date)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS diary_entries")

