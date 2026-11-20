"""Store each school's public enquiry form on the school row.

Revision ID: 0096
Revises: 0095

The form is a small JSON document (intro and which of the fixed fields are
shown). Public submissions still land in admission_enquiries. No enquiry
rows are removed.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0096"
down_revision: Union[str, None] = "0095"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schools",
        sa.Column("admission_form", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("schools", "admission_form")
