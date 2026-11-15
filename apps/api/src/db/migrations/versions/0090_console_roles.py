"""Map platform employees onto Super Admin, Admin, and Sales.

Revision ID: 0090
Revises: 0089

admin@sybeez.ai stays the protected Super Admin. silverstalan.inbaraj@sybeez.ai
stays Super Admin. Other SUPER_ADMIN rows stay. SALES stays. USER becomes Sales.
The older department labels become Admin.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0090"
down_revision: Union[str, None] = "0089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users
        SET platform_role = 'SUPER_ADMIN', is_platform_admin = true
        WHERE lower(email) IN ('admin@sybeez.ai', 'silverstalan.inbaraj@sybeez.ai')
        """
    )
    op.execute(
        """
        UPDATE users
        SET platform_role = 'ADMIN'
        WHERE is_platform_admin IS TRUE
          AND lower(email) NOT IN ('admin@sybeez.ai', 'silverstalan.inbaraj@sybeez.ai')
          AND upper(replace(coalesce(platform_role, ''), ' ', '_')) NOT IN ('SUPER_ADMIN', 'SALES', 'ADMIN')
          AND upper(replace(coalesce(platform_role, ''), ' ', '_')) <> 'USER'
        """
    )
    op.execute(
        """
        UPDATE users
        SET platform_role = 'SALES'
        WHERE is_platform_admin IS TRUE
          AND lower(email) NOT IN ('admin@sybeez.ai', 'silverstalan.inbaraj@sybeez.ai')
          AND upper(replace(coalesce(platform_role, ''), ' ', '_')) IN ('USER', '')
        """
    )


def downgrade() -> None:
    # Role labels are not reversible without the previous values.
    pass
