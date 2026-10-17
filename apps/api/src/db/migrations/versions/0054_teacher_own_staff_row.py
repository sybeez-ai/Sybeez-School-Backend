"""Let a teacher read and provision only their own staff row.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-11

Teacher payslips and self-attendance look up ``employees``. That table is
staff-scoped, so a teacher login saw no row and could not insert their own.
0051 and 0052 stay reserved for the HRM integration branch.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0054"
down_revision: Union[str, None] = "0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        r"""
CREATE OR REPLACE FUNCTION app.can_see(
  category text,
  row_student uuid,
  row_class uuid,
  row_user uuid
) RETURNS boolean
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = public
AS $fn$
DECLARE
  r text := NULLIF(current_setting('app.role', true), '');
  uid uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
  sid uuid := NULLIF(current_setting('app.school_id', true), '')::uuid;
BEGIN
  IF current_setting('app.rls_bypass', true) = 'on' THEN
    RETURN true;
  END IF;
  IF r IS NULL OR sid IS NULL THEN
    RETURN false;
  END IF;
  IF r IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE') THEN
    RETURN true;
  END IF;
  IF uid IS NULL THEN
    RETURN false;
  END IF;

  IF r = 'PLATFORM_STAFF' THEN
    IF category IN ('people', 'catalog') AND (
      rls_staff_portal('finance') OR rls_staff_portal('school_admin')
    ) THEN
      RETURN true;
    END IF;
    IF category = 'staff' AND (
      rls_staff_portal('hrm') OR rls_staff_portal('school_admin')
    ) THEN
      RETURN true;
    END IF;
    IF category = 'finance' AND (
      rls_staff_portal('finance') OR rls_staff_portal('school_admin')
    ) THEN
      RETURN true;
    END IF;
    RETURN false;
  END IF;

  IF category = 'catalog' THEN
    RETURN r IN ('TEACHER', 'STUDENT', 'PARENT', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE');
  END IF;

  IF category = 'audit' THEN
    RETURN r IN ('AUDITOR', 'ACCOUNTANT');
  END IF;

  IF category = 'staff' THEN
    IF r = 'TEACHER' AND row_user IS NOT NULL AND row_user = uid THEN
      RETURN true;
    END IF;
    RETURN r IN (
      'HR_MANAGER', 'HR', 'PAYROLL_ADMIN', 'PAYROLL_APPROVER', 'ACCOUNTANT', 'AUDITOR'
    );
  END IF;

  IF category = 'user_private' THEN
    RETURN row_user IS NOT NULL AND row_user = uid;
  END IF;

  IF category = 'inbox' THEN
    IF row_user IS NOT NULL AND row_user = uid THEN
      RETURN true;
    END IF;
    IF row_class IS NOT NULL THEN
      RETURN app.conversation_member(row_class);
    END IF;
    RETURN false;
  END IF;

  IF category = 'finance' THEN
    IF r IN ('ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE') THEN
      RETURN true;
    END IF;
    IF r = 'STUDENT' THEN
      RETURN row_student IS NOT NULL AND row_student = (
        SELECT s.id FROM students s WHERE s.user_id = uid AND s.school_id = sid LIMIT 1
      );
    END IF;
    IF r = 'PARENT' THEN
      RETURN row_student IS NOT NULL AND EXISTS (
        SELECT 1
        FROM student_parents sp
        JOIN parents p ON p.id = sp.parent_id
        WHERE sp.student_id = row_student
          AND p.user_id = uid
          AND sp.school_id = sid
          AND p.school_id = sid
      );
    END IF;
    RETURN false;
  END IF;

  IF category = 'people' OR category = 'student_data' THEN
    IF r IN ('ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE') AND category = 'people' THEN
      RETURN true;
    END IF;
    IF r = 'STUDENT' THEN
      IF row_student IS NOT NULL THEN
        RETURN row_student = (
          SELECT s.id FROM students s WHERE s.user_id = uid AND s.school_id = sid LIMIT 1
        );
      END IF;
      IF row_class IS NOT NULL THEN
        RETURN row_class IN (
          SELECT e.class_id
          FROM enrollments e
          JOIN students s ON s.id = e.student_id
          WHERE s.user_id = uid AND e.school_id = sid AND e.status = 'ACTIVE'
        );
      END IF;
      RETURN row_user = uid;
    END IF;
    IF r = 'PARENT' THEN
      IF row_student IS NOT NULL THEN
        RETURN EXISTS (
          SELECT 1
          FROM student_parents sp
          JOIN parents p ON p.id = sp.parent_id
          WHERE sp.student_id = row_student
            AND p.user_id = uid
            AND sp.school_id = sid
            AND p.school_id = sid
        );
      END IF;
      IF row_class IS NOT NULL THEN
        RETURN EXISTS (
          SELECT 1
          FROM enrollments e
          JOIN student_parents sp ON sp.student_id = e.student_id
          JOIN parents p ON p.id = sp.parent_id
          WHERE e.class_id = row_class
            AND p.user_id = uid
            AND e.school_id = sid
            AND e.status = 'ACTIVE'
        );
      END IF;
      RETURN row_user = uid;
    END IF;
    IF r = 'TEACHER' THEN
      RETURN row_user = uid
        OR (
          row_student IS NOT NULL AND EXISTS (
            SELECT 1
            FROM enrollments e
            JOIN teacher_assignments ta ON ta.class_id = e.class_id AND ta.school_id = e.school_id
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE e.student_id = row_student
              AND t.user_id = uid
              AND e.school_id = sid
              AND e.status = 'ACTIVE'
          )
        )
        OR (
          row_class IS NOT NULL AND EXISTS (
            SELECT 1
            FROM teacher_assignments ta
            JOIN teachers t ON t.id = ta.teacher_id
            WHERE ta.class_id = row_class AND t.user_id = uid AND ta.school_id = sid
          )
        );
    END IF;
    RETURN false;
  END IF;

  IF category = 'class_content' THEN
    IF row_class IS NULL THEN
      RETURN r IN ('TEACHER', 'STUDENT', 'PARENT');
    END IF;
    IF r = 'TEACHER' THEN
      RETURN EXISTS (
        SELECT 1
        FROM teacher_assignments ta
        JOIN teachers t ON t.id = ta.teacher_id
        WHERE ta.class_id = row_class AND t.user_id = uid AND ta.school_id = sid
      );
    END IF;
    IF r = 'STUDENT' THEN
      RETURN EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN students s ON s.id = e.student_id
        WHERE e.class_id = row_class AND s.user_id = uid AND e.school_id = sid AND e.status = 'ACTIVE'
      );
    END IF;
    IF r = 'PARENT' THEN
      RETURN EXISTS (
        SELECT 1
        FROM enrollments e
        JOIN student_parents sp ON sp.student_id = e.student_id
        JOIN parents p ON p.id = sp.parent_id
        WHERE e.class_id = row_class
          AND p.user_id = uid
          AND e.school_id = sid
          AND e.status = 'ACTIVE'
      );
    END IF;
    RETURN false;
  END IF;

  RETURN false;
END;
$fn$;
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.can_see(text, uuid, uuid, uuid) OWNER TO sybeez_definer;
          END IF;
        END $$;
        """
    )


def downgrade() -> None:
    pass
