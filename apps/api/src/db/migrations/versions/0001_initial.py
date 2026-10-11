"""Phase-1 Sybeez School schema snapshot.

Revision ID: 0001
Revises:
Create Date: 2026-03-20

The table DDL is frozen in ``0001_initial_snapshot.sql``. That is the schema
from the initial models, not ``Base.metadata.create_all`` of whatever the
models look like today. A live ``create_all`` already includes columns and
tables that 0002 and later revisions add, so ``alembic upgrade head`` fails
on an empty database.
"""

from pathlib import Path
from typing import Sequence, Union

from alembic import op

from src.db.migrations.sqlutil import sql_statements

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SNAPSHOT = Path(__file__).with_name("0001_initial_snapshot.sql")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    for statement in sql_statements(_SNAPSHOT.read_text()):
        op.execute(statement)
    # 0002 updates platform_role and does not add the column. cognito_sub is
    # on the user model and was only added by the seed script.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS platform_role VARCHAR(32)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_platform_role ON users (platform_role)")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS cognito_sub VARCHAR(128)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_cognito_sub ON users (cognito_sub)")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_student_ai_memory_embedding_hnsw
        ON student_ai_memory
        USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    from src.db.base import Base
    from src.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
