"""school_fee_profiles: UPI payee and GST identity per school (gateway-agnostic fee collection).

Revision ID: 0075
Revises: 0074
Create Date: 2026-10-18

RLS (per operation, forced): SELECT for users of that school (the UPI QR and receipts need the payee, which is public
information printed on every receipt) and PLATFORM_ADMIN. INSERT/UPDATE/DELETE: SCHOOL_ADMIN or ACCOUNTANT of that school.
No personal data about a person: a school's payee VPA/GSTIN are business identifiers. updated_by is an audit pointer.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0075"
down_revision: Union[str, None] = "0074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

T = "school_fee_profiles"
SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import fee as models

    del models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[T]], checkfirst=True)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {T} TO sybeez_app")
    op.execute(f"GRANT SELECT ON {T} TO sybeez_definer")
    op.execute(f"ALTER TABLE {T} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {T} FORCE ROW LEVEL SECURITY")
    for old in ("select", "insert", "update", "delete"):
        op.execute(f"DROP POLICY IF EXISTS {T}_{old} ON {T}")
    read = f"{BYPASS} OR {ROLE} IN ('PLATFORM_ADMIN', 'SERVICE') OR {SCHOOL}"
    write = f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN ('SCHOOL_ADMIN', 'ACCOUNTANT'))"
    op.execute(f"CREATE POLICY {T}_select ON {T} FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY {T}_insert ON {T} FOR INSERT WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {T}_update ON {T} FOR UPDATE USING ({write}) WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {T}_delete ON {T} FOR DELETE USING ({write})")


def downgrade() -> None:
    for op_name in ("select", "insert", "update", "delete"):
        op.execute(f"DROP POLICY IF EXISTS {T}_{op_name} ON {T}")
    op.execute(f"DROP TABLE IF EXISTS {T}")
