"""Razorpay Route linked accounts and per-payment transfers.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-11

`alembic heads` on release is 0053 (WhatsApp alerts, down_revision 0050).
0051 and 0052 stay reserved for the hrm-integration branch, so this revision
is the next free id: 0054.
"""

from typing import Sequence, Union

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ROUTE_STATUSES = (
    "not_started",
    "created",
    "needs_clarification",
    "under_review",
    "activated",
    "suspended",
)
_TRANSFER_STATUSES = ("created", "pending", "processed", "failed", "settled", "reversed")


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        ALTER TABLE fee_settings
          ADD COLUMN IF NOT EXISTS linked_account_id VARCHAR(64),
          ADD COLUMN IF NOT EXISTS route_product_id VARCHAR(64),
          ADD COLUMN IF NOT EXISTS stakeholder_id VARCHAR(64),
          ADD COLUMN IF NOT EXISTS route_status VARCHAR(32) NOT NULL DEFAULT 'not_started',
          ADD COLUMN IF NOT EXISTS platform_fee_percent NUMERIC(7,4) NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS platform_fee_fixed NUMERIC(12,2),
          ADD COLUMN IF NOT EXISTS settlement_on_hold BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN IF NOT EXISTS settlement_hold_until TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS allow_platform_settlement BOOLEAN NOT NULL DEFAULT FALSE,
          ADD COLUMN IF NOT EXISTS bank_account_last4 VARCHAR(4),
          ADD COLUMN IF NOT EXISTS bank_account_ciphertext TEXT,
          ADD COLUMN IF NOT EXISTS bank_ifsc VARCHAR(16),
          ADD COLUMN IF NOT EXISTS beneficiary_name VARCHAR(128),
          ADD COLUMN IF NOT EXISTS legal_business_name VARCHAR(255)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_settings_linked_account
          ON fee_settings (linked_account_id) WHERE linked_account_id IS NOT NULL
        """
    )
    statuses = ", ".join(f"'{item}'" for item in _ROUTE_STATUSES)
    op.execute(
        f"""
        DO $$ BEGIN
          ALTER TABLE fee_settings
            ADD CONSTRAINT ck_fee_settings_route_status
            CHECK (route_status IN ({statuses}));
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )

    transfer_statuses = ", ".join(f"'{item}'" for item in _TRANSFER_STATUSES)
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS fee_route_transfers (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            payment_id UUID NOT NULL REFERENCES fee_payments(id) ON DELETE CASCADE,
            linked_account_id VARCHAR(64) NOT NULL,
            gateway_order_id VARCHAR(128),
            gateway_payment_id VARCHAR(128),
            gateway_transfer_id VARCHAR(128),
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            payment_amount NUMERIC(12,2) NOT NULL,
            transfer_amount NUMERIC(12,2) NOT NULL,
            platform_fee_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            on_hold BOOLEAN NOT NULL DEFAULT FALSE,
            status VARCHAR(32) NOT NULL DEFAULT 'created',
            failure_reason TEXT,
            settlement_id VARCHAR(128),
            settled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_route_transfer_payment UNIQUE (payment_id),
            CONSTRAINT ck_fee_route_transfer_status CHECK (status IN ({transfer_statuses}))
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_route_transfers_school_id ON fee_route_transfers (school_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fee_route_transfers_linked_account ON fee_route_transfers (linked_account_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_fee_route_transfers_status ON fee_route_transfers (school_id, status)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_route_transfer_gateway
          ON fee_route_transfers (gateway_transfer_id) WHERE gateway_transfer_id IS NOT NULL
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON fee_route_transfers TO sybeez_app")

    op.execute("ALTER TABLE fee_route_transfers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE fee_route_transfers FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS fee_route_transfers_school ON fee_route_transfers")
    op.execute("DROP POLICY IF EXISTS fee_route_transfers_role ON fee_route_transfers")
    op.execute(
        """
        CREATE POLICY fee_route_transfers_school ON fee_route_transfers
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
          school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY fee_route_transfers_role ON fee_route_transfers AS RESTRICTIVE
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR app.can_see('finance', NULL::uuid, NULL::uuid, NULL::uuid)
        )
        WITH CHECK (app.can_see('finance', NULL::uuid, NULL::uuid, NULL::uuid))
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP POLICY IF EXISTS fee_route_transfers_role ON fee_route_transfers")
    op.execute("DROP POLICY IF EXISTS fee_route_transfers_school ON fee_route_transfers")
    op.execute("ALTER TABLE IF EXISTS fee_route_transfers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE IF EXISTS fee_route_transfers DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS fee_route_transfers")
    op.execute("ALTER TABLE fee_settings DROP CONSTRAINT IF EXISTS ck_fee_settings_route_status")
    op.execute("DROP INDEX IF EXISTS uq_fee_settings_linked_account")
    for column in (
        "legal_business_name",
        "beneficiary_name",
        "bank_ifsc",
        "bank_account_ciphertext",
        "bank_account_last4",
        "allow_platform_settlement",
        "settlement_hold_until",
        "settlement_on_hold",
        "platform_fee_fixed",
        "platform_fee_percent",
        "route_status",
        "stakeholder_id",
        "route_product_id",
        "linked_account_id",
    ):
        op.execute(f"ALTER TABLE fee_settings DROP COLUMN IF EXISTS {column}")
