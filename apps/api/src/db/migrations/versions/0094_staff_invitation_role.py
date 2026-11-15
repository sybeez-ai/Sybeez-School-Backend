"""Store the console role on a platform staff invitation.

Revision ID: 0094
Revises: 0093

An email invite can grant Super Admin, Admin, or Sales. The role is kept on the
invitation until it is accepted. Cancelling an invite does not remove a person.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0094"
down_revision: Union[str, None] = "0093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "platform_staff_invitations",
        sa.Column("platform_role", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_staff_invitations", "platform_role")
