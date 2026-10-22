"""School expense heads, vouchers, and an append-only expense ledger.

Revision ID: 0060
Revises: 0059
Create Date: 2026-10-11

Tables are created from the ORM. Row-level security matches fee transfers:
a school policy plus a restrictive finance policy.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0060"
down_revision: Union[str, None] = "0059"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("expense_ledger_entries", "school_expenses", "expense_heads")


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import finance as finance_models

    del finance_models
    tables = [Base.metadata.tables[name] for name in reversed(_TABLES)]
    Base.metadata.create_all(op.get_bind(), tables=tables, checkfirst=True)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION expense_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS expense_ledger_entries_append_only ON expense_ledger_entries")
    op.execute(
        """
        CREATE TRIGGER expense_ledger_entries_append_only
        BEFORE UPDATE OR DELETE ON expense_ledger_entries
        FOR EACH ROW EXECUTE FUNCTION expense_append_only()
        """
    )
    for table in _TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_school ON {table}
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            WITH CHECK (
              school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            """
        )
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_role ON {table} AS RESTRICTIVE
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR app.can_see('finance', NULL::uuid, NULL::uuid, NULL::uuid)
            )
            WITH CHECK (app.can_see('finance', NULL::uuid, NULL::uuid, NULL::uuid))
            """
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS expense_ledger_entries_append_only ON expense_ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS expense_append_only()")
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(f"DROP TABLE IF EXISTS {table}")
