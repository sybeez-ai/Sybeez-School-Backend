"""Per-school plan price, discount, trial window and renewal date.

Revision ID: 0087
Revises: 0086

The subscription row already stores plan code, status and a start/end.
These columns are the commercial terms the platform owner edits for one school.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: Union[str, None] = "0086"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("price_per_student", sa.Numeric(12, 2), nullable=True))
    op.add_column("subscriptions", sa.Column("discount_percent", sa.Numeric(6, 2), nullable=True))
    op.add_column("subscriptions", sa.Column("discount_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("subscriptions", sa.Column("discount_reason", sa.Text(), nullable=True))
    op.add_column("subscriptions", sa.Column("trial_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("subscriptions", sa.Column("renewal_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE subscriptions
        SET trial_started_at = starts_at,
            trial_ends_at = ends_at
        WHERE status = 'TRIAL' AND trial_started_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE subscriptions
        SET renewal_at = ends_at
        WHERE status = 'ACTIVE' AND renewal_at IS NULL AND ends_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "renewal_at")
    op.drop_column("subscriptions", "trial_ends_at")
    op.drop_column("subscriptions", "trial_started_at")
    op.drop_column("subscriptions", "discount_reason")
    op.drop_column("subscriptions", "discount_amount")
    op.drop_column("subscriptions", "discount_percent")
    op.drop_column("subscriptions", "price_per_student")
