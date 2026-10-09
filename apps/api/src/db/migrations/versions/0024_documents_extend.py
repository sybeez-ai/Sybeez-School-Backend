"""Phase 16 Documents: extend documents table with category, description,
visibility, is_active, file_key, file_name, file_size columns.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-05
"""

from typing import Sequence, Union

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS category    VARCHAR(32)  NOT NULL DEFAULT 'STUDY_MATERIAL',
            ADD COLUMN IF NOT EXISTS description TEXT,
            ADD COLUMN IF NOT EXISTS visibility  VARCHAR(32)  NOT NULL DEFAULT 'STUDENTS',
            ADD COLUMN IF NOT EXISTS is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS file_key    TEXT,
            ADD COLUMN IF NOT EXISTS file_name   VARCHAR(255),
            ADD COLUMN IF NOT EXISTS file_size   INTEGER
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_category   ON documents (category)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_visibility ON documents (visibility)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_is_active  ON documents (is_active)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_class_id   ON documents (class_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_subject_id ON documents (subject_id)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS ix_documents_subject_id")
    op.execute("DROP INDEX IF EXISTS ix_documents_class_id")
    op.execute("DROP INDEX IF EXISTS ix_documents_is_active")
    op.execute("DROP INDEX IF EXISTS ix_documents_visibility")
    op.execute("DROP INDEX IF EXISTS ix_documents_category")
    op.execute(
        """
        ALTER TABLE documents
            DROP COLUMN IF EXISTS category,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS visibility,
            DROP COLUMN IF EXISTS is_active,
            DROP COLUMN IF EXISTS file_key,
            DROP COLUMN IF EXISTS file_name,
            DROP COLUMN IF EXISTS file_size
        """
    )
