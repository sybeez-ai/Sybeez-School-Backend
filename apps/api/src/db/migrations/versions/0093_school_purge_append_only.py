"""Let a platform school purge delete append-only finance rows.

Revision ID: 0093
Revises: 0092

fee_ledger_entries, fee_audit_logs, and expense_ledger_entries raise on every
UPDATE or DELETE. A Super Admin erase then stops with those rows still present,
and students cannot go while the ledger still points at them. DELETE is allowed
only while app.rls_bypass is on. UPDATE stays rejected.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0093"
down_revision: Union[str, None] = "0092"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FUNCTION = """
CREATE OR REPLACE FUNCTION {name}() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND current_setting('app.rls_bypass', true) = 'on' THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    op.execute(_FUNCTION.format(name="fee_append_only"))
    op.execute(_FUNCTION.format(name="expense_append_only"))


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fee_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION expense_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
