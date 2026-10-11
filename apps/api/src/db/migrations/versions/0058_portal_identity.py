"""Link HR employees to one login and show staff only published payslips.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-11

Production is already stamped at 0053 (WhatsApp). This revision follows the
HR operations tables. It backfills ``employees.user_id`` from the teaching
record, keeps one employee per login inside a school, copies ``employee_id``
onto payslips that only had a teacher, and changes ``app.payroll_owns_payslip``
so the owner sees a row only after it is published or paid. Payroll staff
still see drafts through ``app.payroll_is_staff()``.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE employees e
        SET user_id = t.user_id
        FROM teachers t
        WHERE e.teacher_id = t.id
          AND e.user_id IS NULL
          AND t.user_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM employees other
            WHERE other.school_id = e.school_id
              AND other.user_id = t.user_id
              AND other.id <> e.id
          )
        """
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (
            PARTITION BY school_id, user_id
            ORDER BY CASE WHEN staff_type = 'TEACHING' THEN 0 ELSE 1 END, created_at, id
          ) AS n
          FROM employees
          WHERE user_id IS NOT NULL
        )
        UPDATE employees SET user_id = NULL
        WHERE id IN (SELECT id FROM ranked WHERE n > 1)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_school_user
          ON employees (school_id, user_id)
          WHERE user_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE payroll_payslips p
        SET employee_id = e.id
        FROM employees e
        WHERE p.employee_id IS NULL
          AND p.teacher_id IS NOT NULL
          AND e.teacher_id = p.teacher_id
          AND e.school_id = p.school_id
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.payroll_owns_payslip(pid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT EXISTS (
            SELECT 1 FROM payroll_payslips p
            WHERE p.id = pid
              AND p.status IN ('PUBLISHED', 'PAID')
              AND (
                app.payroll_owns_employee(p.employee_id)
                OR (
                  p.teacher_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM teachers t
                    WHERE t.id = p.teacher_id
                      AND t.school_id = p.school_id
                      AND t.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                  )
                )
              )
          )
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.payroll_owns_payslip(uuid) OWNER TO sybeez_definer;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_employees_school_user")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.payroll_owns_payslip(pid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT EXISTS (
            SELECT 1 FROM payroll_payslips p
            WHERE p.id = pid
              AND (
                app.payroll_owns_employee(p.employee_id)
                OR (
                  p.teacher_id IS NOT NULL AND EXISTS (
                    SELECT 1 FROM teachers t
                    WHERE t.id = p.teacher_id
                      AND t.school_id = p.school_id
                      AND t.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                  )
                )
              )
          )
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.payroll_owns_payslip(uuid) OWNER TO sybeez_definer;
          END IF;
        END $$
        """
    )
