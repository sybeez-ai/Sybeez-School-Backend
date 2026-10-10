"""HRM Phase 1: payroll/leave integrity constraints and same-school composite foreign keys.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, constraint name, DDL body) — added only when missing so the migration is re-runnable.
_CONSTRAINTS: list[tuple[str, str, str]] = [
    ("teachers", "uq_teachers_id_school_id", "UNIQUE (id, school_id)"),
    ("payroll_runs", "uq_payroll_runs_id_school_id", "UNIQUE (id, school_id)"),
    (
        "payroll_payslips",
        "fk_payroll_payslips_run_same_school",
        "FOREIGN KEY (payroll_run_id, school_id) REFERENCES payroll_runs (id, school_id) ON DELETE CASCADE",
    ),
    (
        "payroll_payslips",
        "fk_payroll_payslips_teacher_same_school",
        "FOREIGN KEY (teacher_id, school_id) REFERENCES teachers (id, school_id) ON DELETE CASCADE",
    ),
    ("payroll_payslips", "ck_payroll_payslips_gross_non_negative", "CHECK (gross_amount >= 0)"),
    ("payroll_payslips", "ck_payroll_payslips_deductions_non_negative", "CHECK (deductions_amount >= 0)"),
    ("payroll_payslips", "ck_payroll_payslips_net_non_negative", "CHECK (net_amount >= 0)"),
    (
        "payroll_payslips",
        "ck_payroll_payslips_status_allowed",
        "CHECK (status IN ('DRAFT', 'PAID', 'CANCELLED'))",
    ),
    (
        "payroll_payslips",
        "ck_payroll_payslips_paid_has_date",
        "CHECK (status <> 'PAID' OR paid_on IS NOT NULL)",
    ),
    ("payroll_runs", "ck_payroll_runs_period_order", "CHECK (period_end >= period_start)"),
    ("staff_leave_requests", "ck_staff_leave_requests_date_order", "CHECK (end_date >= start_date)"),
    (
        "staff_leave_requests",
        "ck_staff_leave_requests_status_allowed",
        "CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED'))",
    ),
]

# Existing rows that would make the constraints above fail; checked first so the error is actionable.
_VIOLATIONS: list[tuple[str, str]] = [
    (
        "payslips whose school differs from their payroll run",
        (
            "SELECT count(*) FROM payroll_payslips s JOIN payroll_runs r ON r.id = s.payroll_run_id "
            "WHERE r.school_id <> s.school_id"
        ),
    ),
    (
        "payslips whose school differs from their teacher",
        (
            "SELECT count(*) FROM payroll_payslips s JOIN teachers t ON t.id = s.teacher_id "
            "WHERE t.school_id <> s.school_id"
        ),
    ),
    (
        "payslips with negative amounts",
        (
            "SELECT count(*) FROM payroll_payslips "
            "WHERE gross_amount < 0 OR deductions_amount < 0 OR net_amount < 0"
        ),
    ),
    (
        "payslips with an unknown status",
        "SELECT count(*) FROM payroll_payslips WHERE status NOT IN ('DRAFT', 'PAID', 'CANCELLED')",
    ),
    (
        "PAID payslips without paid_on",
        "SELECT count(*) FROM payroll_payslips WHERE status = 'PAID' AND paid_on IS NULL",
    ),
    (
        "duplicate active payslips for one teacher in one run",
        (
            "SELECT count(*) FROM (SELECT 1 FROM payroll_payslips WHERE status <> 'CANCELLED' "
            "GROUP BY payroll_run_id, teacher_id HAVING count(*) > 1) d"
        ),
    ),
    ("payroll runs ending before they start", "SELECT count(*) FROM payroll_runs WHERE period_end < period_start"),
    (
        "leave requests ending before they start",
        "SELECT count(*) FROM staff_leave_requests WHERE end_date < start_date",
    ),
    (
        "leave requests with an unknown status",
        (
            "SELECT count(*) FROM staff_leave_requests "
            "WHERE status NOT IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')"
        ),
    ),
]


def upgrade() -> None:
    from alembic import op
    from sqlalchemy import text

    bind = op.get_bind()
    problems = []
    for label, sql in _VIOLATIONS:
        n = bind.execute(text(sql)).scalar_one()
        if n:
            problems.append(f"{n} {label}")
    if problems:
        raise RuntimeError(
            "Cannot add HRM integrity constraints; fix these rows first: " + "; ".join(problems)
        )

    for table, name, body in _CONSTRAINTS:
        op.execute(
            f"""
            DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                    ALTER TABLE {table} ADD CONSTRAINT {name} {body};
                END IF;
            END $$;
            """
        )

    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_payslips_run_teacher_active "
        "ON payroll_payslips (payroll_run_id, teacher_id) WHERE status <> 'CANCELLED'"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP INDEX IF EXISTS uq_payroll_payslips_run_teacher_active")
    for table, name, _ in reversed(_CONSTRAINTS):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")
