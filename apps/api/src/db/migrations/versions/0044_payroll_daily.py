"""Loan ledger and loss-of-pay audit for daily payroll.

Revision ID: 0044
Revises: 0043

Renumbered from 0041. Finance already uses 0041–0043.
"""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None

_CREATE_EVENTS = """
CREATE TABLE IF NOT EXISTS payroll_loan_events (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  loan_id UUID NOT NULL REFERENCES payroll_loans(id) ON DELETE CASCADE,
  payslip_id UUID,
  kind VARCHAR(16) NOT NULL,
  principal NUMERIC(12,2) NOT NULL DEFAULT 0,
  interest NUMERIC(12,2) NOT NULL DEFAULT 0,
  balance_after NUMERIC(12,2) NOT NULL,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT loan_event_amounts CHECK (principal >= 0 AND interest >= 0 AND balance_after >= 0),
  CONSTRAINT loan_event_kind CHECK (kind IN ('EMI', 'PREPAY', 'CLOSE', 'REVERSAL'))
)
"""

_SCHOOL_SQL = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
)
_ROLE_SQL = "app.payroll_is_staff()"


def upgrade() -> None:
    op.execute("ALTER TABLE payroll_payslips ADD COLUMN IF NOT EXISTS lop_detail JSONB")
    op.execute(_CREATE_EVENTS)
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_loan_events_school_id ON payroll_loan_events (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_loan_events_loan_id ON payroll_loan_events (loan_id)")
    op.execute("ALTER TABLE payroll_loan_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_loan_events FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS payroll_loan_events_school ON payroll_loan_events")
    op.execute("DROP POLICY IF EXISTS payroll_loan_events_role ON payroll_loan_events")
    op.execute(
        f"""
        CREATE POLICY payroll_loan_events_school ON payroll_loan_events
        USING ({_SCHOOL_SQL})
        WITH CHECK ({_SCHOOL_SQL})
        """
    )
    op.execute(
        f"""
        CREATE POLICY payroll_loan_events_role ON payroll_loan_events
        USING (current_setting('app.rls_bypass', true) = 'on' OR ({_ROLE_SQL}))
        WITH CHECK (current_setting('app.rls_bypass', true) = 'on' OR ({_ROLE_SQL}))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS payroll_loan_events_role ON payroll_loan_events")
    op.execute("DROP POLICY IF EXISTS payroll_loan_events_school ON payroll_loan_events")
    op.execute("DROP TABLE IF EXISTS payroll_loan_events")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS lop_detail")
