"""Platform RBAC + school visibility grants.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.db.migration_guards import column_exists, create_index_if_missing, table_exists

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migrate legacy STAFF -> USER
    op.execute("UPDATE users SET platform_role = 'USER' WHERE platform_role = 'STAFF'")

    # School creator tracking (visibility for platform USERs)
    if not column_exists("schools", "created_by_id"):
        op.add_column(
            "schools",
            sa.Column(
                "created_by_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
    create_index_if_missing("ix_schools_created_by_id", "schools", ["created_by_id"])

    # Per-school access grants for platform USERs
    if not table_exists("platform_school_access"):
        op.create_table(
            "platform_school_access",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "school_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("schools.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.UniqueConstraint("user_id", "school_id", name="uq_platform_school_access_user_school"),
        )
    create_index_if_missing("ix_platform_school_access_user_id", "platform_school_access", ["user_id"])
    create_index_if_missing("ix_platform_school_access_school_id", "platform_school_access", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_platform_school_access_school_id", table_name="platform_school_access")
    op.drop_index("ix_platform_school_access_user_id", table_name="platform_school_access")
    op.drop_table("platform_school_access")

    op.drop_index("ix_schools_created_by_id", table_name="schools")
    op.drop_column("schools", "created_by_id")

