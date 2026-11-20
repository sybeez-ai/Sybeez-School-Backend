"""Books, school stores, transport routes, and the settings Silver can change.

Revision ID: 0082
Revises: 0081

Additive only. Existing rows keep working. New columns have defaults.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0082"
down_revision: Union[str, None] = "0081"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "ledger_accounts",
    "journal_entries",
    "journal_lines",
    "school_funds",
    "book_vendors",
    "school_budgets",
    "book_bank_accounts",
    "tax_items",
    "book_purchases",
    "book_assets",
    "transport_routes",
    "ops_assets",
    "ops_inventory_items",
    "ops_purchase_orders",
    "ops_maintenance_jobs",
)

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
FINANCE_READ = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN "
    "('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE'))"
)
FINANCE_WRITE = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN "
    "('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT'))"
)
OPS_WRITE = f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE'))"
OPS_TABLES = {"ops_assets", "ops_inventory_items", "ops_purchase_orders", "ops_maintenance_jobs"}


def upgrade() -> None:
    op.add_column(
        "payroll_school_settings",
        sa.Column(
            "unmarked_attendance_policy",
            sa.String(32),
            nullable=False,
            server_default="DO_NOT_DEDUCT",
        ),
    )
    op.add_column(
        "fee_settings",
        sa.Column("tally_ledger_map", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    # Empty template means "use the built-in 80C wording" so schools can replace it later.
    op.add_column(
        "fee_settings",
        sa.Column("section_80c_template", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "fee_settings",
        sa.Column("section_80c_signatory", sa.String(160), nullable=False, server_default="Principal"),
    )
    op.add_column(
        "fee_settings",
        sa.Column("financial_year_start_month", sa.Integer(), nullable=False, server_default=sa.text("4")),
    )
    op.add_column(
        "fee_settings",
        sa.Column("section_80c_heads", JSONB, nullable=False, server_default=sa.text("'[\"TUITION\"]'::jsonb")),
    )
    op.add_column(
        "school_alert_settings",
        sa.Column("parent_whatsapp_cloud_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    from src.db.base import Base
    from src.db.models import books as _models

    del _models
    bind = op.get_bind()
    Base.metadata.create_all(bind, tables=[Base.metadata.tables[name] for name in TABLES], checkfirst=True)
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        read = OPS_WRITE if table in OPS_TABLES else FINANCE_READ
        write = OPS_WRITE if table in OPS_TABLES else FINANCE_WRITE
        op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({read})")
        op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write})")
        op.execute(f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})")
        op.execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({write})")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.drop_column("school_alert_settings", "parent_whatsapp_cloud_enabled")
    op.drop_column("fee_settings", "section_80c_heads")
    op.drop_column("fee_settings", "financial_year_start_month")
    op.drop_column("fee_settings", "section_80c_signatory")
    op.drop_column("fee_settings", "section_80c_template")
    op.drop_column("fee_settings", "tally_ledger_map")
    op.drop_column("payroll_school_settings", "unmarked_attendance_policy")
