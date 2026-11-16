"""Store the provider token total and Gemini audio/text splits.

Revision ID: 0095
Revises: 0094

Qwen and Gemini rows keep the provider total, plus audio and text counts from
Gemini usageMetadata. Older rows leave total_tokens at 0 and the readers fall
back to input plus output.

A Platform console operator has no school. Their per-person row uses a null
school id, kept unique per user and model.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0095"
down_revision: Union[str, None] = "0094"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLUMNS = (
    "total_tokens",
    "input_audio_tokens",
    "input_text_tokens",
    "output_audio_tokens",
    "output_text_tokens",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column(
            "platform_ai_usage_daily",
            sa.Column(name, sa.BigInteger(), nullable=False, server_default="0"),
        )
    op.alter_column("platform_ai_user_usage", "school_id", existing_type=sa.Uuid(), nullable=True)
    op.create_index(
        "uq_ai_user_usage_platform_user_model",
        "platform_ai_user_usage",
        ["user_id", "model"],
        unique=True,
        postgresql_where=sa.text("school_id IS NULL"),
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_ai_user_usage_platform_user_model")
    op.execute("DELETE FROM platform_ai_user_usage WHERE school_id IS NULL")
    op.alter_column("platform_ai_user_usage", "school_id", existing_type=sa.Uuid(), nullable=False)
    for name in reversed(_COLUMNS):
        op.drop_column("platform_ai_usage_daily", name)
