"""Phase 15 HR: staff leave + simple payroll runs/payslips.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_leave_requests (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_leave_requests_school_id ON staff_leave_requests (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_leave_requests_user_id ON staff_leave_requests (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_leave_requests_status ON staff_leave_requests (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_leave_requests_start_date ON staff_leave_requests (start_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_staff_leave_requests_end_date ON staff_leave_requests (end_date)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS payroll_runs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            label VARCHAR(120) NOT NULL,
            period_start DATE NOT NULL,
            period_end DATE NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            finalized_by UUID REFERENCES users(id) ON DELETE SET NULL,
            finalized_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_runs_school_id ON payroll_runs (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_runs_status ON payroll_runs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_runs_period_start ON payroll_runs (period_start)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_runs_period_end ON payroll_runs (period_end)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS payroll_payslips (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            payroll_run_id UUID NOT NULL REFERENCES payroll_runs(id) ON DELETE CASCADE,
            teacher_id UUID NOT NULL REFERENCES teachers(id) ON DELETE CASCADE,
            gross_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            deductions_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            net_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            paid_on DATE,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_payslips_school_id ON payroll_payslips (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_payslips_payroll_run_id ON payroll_payslips (payroll_run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_payslips_teacher_id ON payroll_payslips (teacher_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_payslips_status ON payroll_payslips (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payroll_payslips_paid_on ON payroll_payslips (paid_on)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS payroll_payslips CASCADE")
    op.execute("DROP TABLE IF EXISTS payroll_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS staff_leave_requests CASCADE")

