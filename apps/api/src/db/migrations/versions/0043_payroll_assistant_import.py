"""Payroll assistant imports and opening year-to-date figures.

Revision ID: 0043
Revises: 0042
"""

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_IMPORTS = """
CREATE TABLE IF NOT EXISTS payroll_assistant_imports (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  filename VARCHAR(255) NOT NULL,
  content_type VARCHAR(120) NOT NULL DEFAULT 'application/octet-stream',
  status VARCHAR(16) NOT NULL DEFAULT 'PREVIEW',
  mapping JSONB NOT NULL DEFAULT '{}'::jsonb,
  preview JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key VARCHAR(80),
  result JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT assistant_import_status CHECK (status IN ('PREVIEW', 'IMPORTED'))
)
"""

_YTD = """
CREATE TABLE IF NOT EXISTS payroll_opening_ytd (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  financial_year VARCHAR(9) NOT NULL,
  ytd_gross NUMERIC(14, 2) NOT NULL DEFAULT 0,
  ytd_tds NUMERIC(14, 2) NOT NULL DEFAULT 0,
  ytd_pf NUMERIC(14, 2) NOT NULL DEFAULT 0,
  ytd_esi NUMERIC(14, 2) NOT NULL DEFAULT 0,
  ytd_pt NUMERIC(14, 2) NOT NULL DEFAULT 0,
  source VARCHAR(32) NOT NULL DEFAULT 'IMPORT',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_opening_ytd_employee_fy UNIQUE (employee_id, financial_year),
  CONSTRAINT fk_opening_ytd_employee_school FOREIGN KEY (employee_id, school_id)
    REFERENCES employees (id, school_id) ON DELETE CASCADE
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


def _protect(table: str, *, owner_column: str | None) -> None:
    op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_school_id ON {table} (school_id)")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_school ON {table}
        USING ({_SCHOOL})
        WITH CHECK ({_SCHOOL})
        """
    )
    if owner_column:
        role = _ROLE if owner_column == "user_id" else "app.payroll_is_staff()"
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_role ON {table}
            USING (current_setting('app.rls_bypass', true) = 'on' OR ({role}))
            WITH CHECK (current_setting('app.rls_bypass', true) = 'on' OR ({role}))
            """
        )


def upgrade() -> None:
    op.execute(_IMPORTS)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_assistant_import_idempotency
        ON payroll_assistant_imports (school_id, user_id, idempotency_key)
        WHERE idempotency_key IS NOT NULL
        """
    )
    _protect("payroll_assistant_imports", owner_column="user_id")
    op.execute(_YTD)
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_opening_ytd_employee_id ON payroll_opening_ytd (employee_id)")
    _protect("payroll_opening_ytd", owner_column="staff")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS payroll_opening_ytd_role ON payroll_opening_ytd")
    op.execute("DROP POLICY IF EXISTS payroll_opening_ytd_school ON payroll_opening_ytd")
    op.execute("DROP TABLE IF EXISTS payroll_opening_ytd")
    op.execute("DROP POLICY IF EXISTS payroll_assistant_imports_role ON payroll_assistant_imports")
    op.execute("DROP POLICY IF EXISTS payroll_assistant_imports_school ON payroll_assistant_imports")
    op.execute("DROP TABLE IF EXISTS payroll_assistant_imports")
