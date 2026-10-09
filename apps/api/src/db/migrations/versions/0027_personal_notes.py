"""Phase 19: Personal notes for students and teachers.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-05
"""

from typing import Sequence, Union

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS personal_notes (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            subject VARCHAR(120) NOT NULL DEFAULT '',
            chapter VARCHAR(200) NOT NULL DEFAULT '',
            title VARCHAR(200) NOT NULL,
            body TEXT NOT NULL,
            tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            color VARCHAR(32) NOT NULL DEFAULT 'blue',
            pinned BOOLEAN NOT NULL DEFAULT FALSE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_personal_notes_school_id ON personal_notes (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_personal_notes_user_id ON personal_notes (user_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_personal_notes_school_user ON personal_notes (school_id, user_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS personal_notes")
