"""Bank statement lines linked to a school bank account.

Revision ID: 0084
Revises: 0083

Additive only.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0084"
down_revision: Union[str, None] = "0083"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "book_bank_lines"
SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
READ = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN "
    "('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE'))"
)
WRITE = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN "
    "('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT'))"
)


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import books as _models

    del _models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[TABLE]], checkfirst=True)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO sybeez_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {TABLE}_select ON {TABLE} FOR SELECT USING ({READ})")
    op.execute(f"CREATE POLICY {TABLE}_insert ON {TABLE} FOR INSERT WITH CHECK ({WRITE})")
    op.execute(f"CREATE POLICY {TABLE}_update ON {TABLE} FOR UPDATE USING ({WRITE}) WITH CHECK ({WRITE})")
    op.execute(f"CREATE POLICY {TABLE}_delete ON {TABLE} FOR DELETE USING ({WRITE})")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
