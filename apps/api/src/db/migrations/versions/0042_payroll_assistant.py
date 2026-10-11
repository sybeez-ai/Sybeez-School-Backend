"""Proposed payroll assistant actions.

Revision ID: 0042
Revises: 0041
"""

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

_CREATE = """
CREATE TABLE IF NOT EXISTS payroll_assistant_actions (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  action VARCHAR(48) NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  summary TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PROPOSED',
  idempotency_key VARCHAR(80),
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT assistant_action_status CHECK (status IN ('PROPOSED', 'EXECUTED'))
)
"""

_SCHOOL = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
)
_ROLE = (
    "app.payroll_is_staff() "
    "OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
)


def upgrade() -> None:
    op.execute(_CREATE)
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_assistant_actions_school_id ON payroll_assistant_actions (school_id)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_assistant_idempotency
        ON payroll_assistant_actions (school_id, user_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute("ALTER TABLE payroll_assistant_actions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payroll_assistant_actions FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS payroll_assistant_actions_school ON payroll_assistant_actions")
    op.execute("DROP POLICY IF EXISTS payroll_assistant_actions_role ON payroll_assistant_actions")
    op.execute(
        f"""
        CREATE POLICY payroll_assistant_actions_school ON payroll_assistant_actions
        USING ({_SCHOOL})
        WITH CHECK ({_SCHOOL})
        """
    )
    op.execute(
        f"""
        CREATE POLICY payroll_assistant_actions_role ON payroll_assistant_actions
        USING (current_setting('app.rls_bypass', true) = 'on' OR ({_ROLE}))
        WITH CHECK (current_setting('app.rls_bypass', true) = 'on' OR ({_ROLE}))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS payroll_assistant_actions_role ON payroll_assistant_actions")
    op.execute("DROP POLICY IF EXISTS payroll_assistant_actions_school ON payroll_assistant_actions")
    op.execute("DROP TABLE IF EXISTS payroll_assistant_actions")
