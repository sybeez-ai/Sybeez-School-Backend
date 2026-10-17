"""Columns the AI service writes on the shared school database.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-10

The AI service uses the same database and inserts `ai_conversations.mode`
and `ai_messages.meta`. Fresh installs already have the columns from 0001
`create_all` of the current models, so these statements no-op then.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE ai_conversations ADD COLUMN IF NOT EXISTS mode VARCHAR(32) NOT NULL DEFAULT 'board'"
    )
    op.execute("ALTER TABLE ai_messages ADD COLUMN IF NOT EXISTS meta JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE ai_messages DROP COLUMN IF EXISTS meta")
    op.execute("ALTER TABLE ai_conversations DROP COLUMN IF EXISTS mode")
