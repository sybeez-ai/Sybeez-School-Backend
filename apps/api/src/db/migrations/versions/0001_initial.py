"""Phase-1 Sybeez School schema: Core facts + AI tables on one PostgreSQL + pgvector.

Revision ID: 0001
Revises:
Create Date: 2026-03-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    from src.db.base import Base
    from src.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)

    # Similarity search indexes (pgvector HNSW)
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding_hnsw
        ON knowledge_chunks
        USING hnsw (embedding vector_cosine_ops);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_student_ai_memory_embedding_hnsw
        ON student_ai_memory
        USING hnsw (embedding vector_cosine_ops);
        """
    )


def downgrade() -> None:
    from src.db.base import Base
    from src.db import models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
