"""Production payroll, staff self-attendance, and forced school RLS.

Revision ID: 0040
Revises: 0035

0040 is numbered past both heads that matter for the merge:
- HRM head is 0035 (this down_revision).
- Backend PR #1 (cursor/finance-production-dde9) ends at revision id 0030.

Do not reuse 0028, 0029, or 0030. Those ids already mean different files on
HRM and on PR #1. Merge order is in apps/api/docs/PAYROLL.md.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0040"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLES = (
    "staff_attendance_period_locks",
    "staff_attendance_regularisations",
    "staff_attendance_sessions",
    "payroll_role_grants",
    "payroll_compliance_events",
    "payroll_settlements",
    "payroll_audit_logs",
    "payroll_payments",
    "payroll_payslip_lines",
    "tax_declaration_proofs",
    "tax_declarations",
    "payroll_reimbursements",
    "payroll_arrears",
    "payroll_loans",
    "pt_slab_rules",
    "weekly_off_rules",
    "school_holidays",
    "employee_bank_accounts",
    "employee_statutory_profiles",
    "employee_salary_assignments",
    "salary_template_lines",
    "salary_templates",
    "salary_components",
    "payroll_setting_revisions",
    "payroll_school_settings",
)

# table -> extra role SQL (already AND-ed with the school policy). None means payroll staff only.
_ROLE_SQL = {
    "payroll_school_settings": "app.payroll_is_staff()",
    "payroll_setting_revisions": "app.payroll_is_staff()",
    "salary_components": "app.payroll_is_staff()",
    "salary_templates": "app.payroll_is_staff()",
    "salary_template_lines": "app.payroll_is_staff()",
    "employee_salary_assignments": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "employee_statutory_profiles": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "employee_bank_accounts": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "school_holidays": ("TRUE", "app.payroll_is_staff()"),
    "weekly_off_rules": ("TRUE", "app.payroll_is_staff()"),
    "pt_slab_rules": ("TRUE", "app.payroll_is_staff()"),
    "payroll_loans": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "payroll_arrears": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "payroll_reimbursements": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "tax_declarations": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "tax_declaration_proofs": "app.payroll_is_staff() OR app.payroll_owns_declaration(declaration_id)",
    "payroll_payslip_lines": "app.payroll_is_staff() OR app.payroll_owns_payslip(payslip_id)",
    "payroll_payments": "app.payroll_is_staff() OR app.payroll_owns_payslip(payslip_id)",
    "payroll_audit_logs": "app.payroll_is_staff()",
    "payroll_settlements": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "payroll_compliance_events": "app.payroll_is_staff()",
    "payroll_role_grants": (
        "app.payroll_is_staff() OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
    ),
    "staff_attendance_sessions": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "staff_attendance_regularisations": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "staff_attendance_period_locks": "app.payroll_is_staff()",
    "payroll_runs": "app.payroll_is_staff()",
    "payroll_payslips": "app.payroll_is_staff() OR app.payroll_owns_payslip(id)",
    "staff_attendance": "app.payroll_is_staff() OR app.payroll_owns_employee(employee_id)",
    "staff_leave_requests": (
        "app.payroll_is_staff() OR user_id = NULLIF(current_setting('app.user_id', true), '')::uuid"
    ),
}

_NULLABLE_SCHOOL = {"pt_slab_rules"}


def upgrade() -> None:
    op.execute(_SCHEMA_SQL)
    _extend_existing()
    op.execute(_TABLES_SQL)
    _rls_functions()
    _seed_pt()
    _policies()


def downgrade() -> None:
    for table in list(_ROLE_SQL):
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(f"ALTER TABLE IF EXISTS {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE IF EXISTS {table} DISABLE ROW LEVEL SECURITY")
    for table in _NEW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("ALTER TABLE payroll_payslips DROP CONSTRAINT IF EXISTS fk_payroll_payslips_employee_school")
    op.execute("DROP INDEX IF EXISTS uq_payroll_payslips_run_employee_active")
    op.execute("DROP INDEX IF EXISTS uq_payroll_runs_statutory_period")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS first_downloaded_at")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS first_viewed_at")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS download_count")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS view_count")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS form16")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS tax_regime")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS published_at")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS pdf_object_key")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS paid_days")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS lop_days")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS working_days")
    op.execute("ALTER TABLE payroll_payslips DROP COLUMN IF EXISTS employee_id")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS published_at")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS locked_at")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS pay_date")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS rejection_reason")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS rejected_by")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS approved_at")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS approved_by")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS submitted_at")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS submitted_by")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS approval_status")
    op.execute("ALTER TABLE payroll_runs DROP COLUMN IF EXISTS run_kind")
    op.execute("ALTER TABLE staff_leave_requests DROP COLUMN IF EXISTS is_paid")
    op.execute("ALTER TABLE payroll_payslips DROP CONSTRAINT IF EXISTS ck_payroll_payslips_status_allowed")
    op.execute(
        "ALTER TABLE payroll_payslips ADD CONSTRAINT ck_payroll_payslips_status_allowed "
        "CHECK (status IN ('DRAFT', 'PAID', 'CANCELLED'))"
    )
    op.execute("ALTER TABLE payroll_payslips ALTER COLUMN teacher_id SET NOT NULL")
    for name in (
        "app.payroll_owns_declaration(uuid)",
        "app.payroll_owns_payslip(uuid)",
        "app.payroll_owns_employee(uuid)",
        "app.payroll_is_staff()",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {name}")


def _rls_functions() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.payroll_is_staff() RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT current_setting('app.rls_bypass', true) = 'on'
            OR current_setting('app.role', true) IN (
              'SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE',
              'PAYROLL_ADMIN', 'HR_MANAGER', 'ACCOUNTANT', 'PAYROLL_APPROVER', 'AUDITOR'
            )
            OR EXISTS (
              SELECT 1 FROM payroll_role_grants g
              WHERE g.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
                AND g.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                AND g.role IN ('PAYROLL_ADMIN', 'HR_MANAGER', 'ACCOUNTANT', 'PAYROLL_APPROVER', 'AUDITOR')
                AND g.effective_from <= CURRENT_DATE
                AND (g.effective_to IS NULL OR g.effective_to >= CURRENT_DATE)
            )
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.payroll_owns_employee(eid uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT eid IS NOT NULL AND EXISTS (
            SELECT 1 FROM employees e
            WHERE e.id = eid
              AND e.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
              AND (
                e.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                OR EXISTS (
                  SELECT 1 FROM teachers t
                  WHERE t.id = e.teacher_id
                    AND t.school_id = e.school_id
                    AND t.user_id = NULLIF(current_setting('app.user_id', true), '')::uuid
                )
              )
          )
        $$;
        """
    )
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
        CREATE OR REPLACE FUNCTION app.payroll_owns_declaration(did uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
          SELECT EXISTS (
            SELECT 1 FROM tax_declarations d
            WHERE d.id = did AND app.payroll_owns_employee(d.employee_id)
          )
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_definer') THEN
            ALTER FUNCTION app.payroll_is_staff() OWNER TO sybeez_definer;
            ALTER FUNCTION app.payroll_owns_employee(uuid) OWNER TO sybeez_definer;
            ALTER FUNCTION app.payroll_owns_payslip(uuid) OWNER TO sybeez_definer;
            ALTER FUNCTION app.payroll_owns_declaration(uuid) OWNER TO sybeez_definer;
          END IF;
        END $$;
        """
    )


def _extend_existing() -> None:
    op.execute("ALTER TABLE staff_leave_requests ADD COLUMN IF NOT EXISTS is_paid BOOLEAN NOT NULL DEFAULT TRUE")
    op.execute(
        """
        ALTER TABLE payroll_runs
          ADD COLUMN IF NOT EXISTS run_kind VARCHAR(16) NOT NULL DEFAULT 'MANUAL',
          ADD COLUMN IF NOT EXISTS approval_status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
          ADD COLUMN IF NOT EXISTS source VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
          ADD COLUMN IF NOT EXISTS submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS rejected_by UUID REFERENCES users(id) ON DELETE SET NULL,
          ADD COLUMN IF NOT EXISTS rejection_reason TEXT,
          ADD COLUMN IF NOT EXISTS pay_date DATE,
          ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ
        """
    )
    op.execute("ALTER TABLE payroll_payslips ALTER COLUMN teacher_id DROP NOT NULL")
    op.execute("ALTER TABLE payroll_payslips ADD COLUMN IF NOT EXISTS employee_id UUID")
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE payroll_payslips
            ADD CONSTRAINT fk_payroll_payslips_employee_school
            FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE;
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE payroll_payslips
          ADD COLUMN IF NOT EXISTS working_days NUMERIC(6,2),
          ADD COLUMN IF NOT EXISTS lop_days NUMERIC(6,2),
          ADD COLUMN IF NOT EXISTS paid_days NUMERIC(6,2),
          ADD COLUMN IF NOT EXISTS pdf_object_key TEXT,
          ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS tax_regime VARCHAR(8),
          ADD COLUMN IF NOT EXISTS form16 JSONB,
          ADD COLUMN IF NOT EXISTS view_count INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS download_count INTEGER NOT NULL DEFAULT 0,
          ADD COLUMN IF NOT EXISTS first_viewed_at TIMESTAMPTZ,
          ADD COLUMN IF NOT EXISTS first_downloaded_at TIMESTAMPTZ
        """
    )
    op.execute("ALTER TABLE payroll_payslips DROP CONSTRAINT IF EXISTS ck_payroll_payslips_status_allowed")
    op.execute(
        "ALTER TABLE payroll_payslips ADD CONSTRAINT ck_payroll_payslips_status_allowed "
        "CHECK (status IN ('DRAFT', 'PUBLISHED', 'PAID', 'CANCELLED'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_payslips_run_employee_active "
        "ON payroll_payslips (payroll_run_id, employee_id) "
        "WHERE employee_id IS NOT NULL AND status <> 'CANCELLED'"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_payroll_runs_statutory_period "
        "ON payroll_runs (school_id, period_start, period_end) "
        "WHERE run_kind = 'STATUTORY' AND approval_status NOT IN ('REJECTED', 'CANCELLED')"
    )


def _seed_pt() -> None:
    op.execute(
        """
        INSERT INTO pt_slab_rules (
          id, school_id, state_code, gender, min_amount, max_amount, amount, february_amount,
          frequency, basis, created_at, updated_at
        )
        SELECT gen_random_uuid(), NULL, state_code, gender, min_amount, max_amount, amount, february_amount,
               frequency, basis, NOW(), NOW()
        FROM (VALUES
          ('KA','ANY',0,24999.99,0,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('KA','ANY',25000,NULL,200,300,'MONTHLY','MONTHLY_GROSS'),
          ('MH','MALE',0,7500,0,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('MH','MALE',7500.01,10000,175,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('MH','MALE',10000.01,NULL,200,300,'MONTHLY','MONTHLY_GROSS'),
          ('MH','FEMALE',0,25000,0,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('MH','FEMALE',25000.01,NULL,200,300,'MONTHLY','MONTHLY_GROSS'),
          ('MH','OTHER',0,7500,0,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('MH','OTHER',7500.01,10000,175,NULL,'MONTHLY','MONTHLY_GROSS'),
          ('MH','OTHER',10000.01,NULL,200,300,'MONTHLY','MONTHLY_GROSS'),
          ('TN','ANY',0,21000,0,NULL,'HALF_YEARLY','HALF_YEAR_GROSS'),
          ('TN','ANY',21000.01,30000,180,NULL,'HALF_YEARLY','HALF_YEAR_GROSS'),
          ('TN','ANY',30000.01,45000,425,NULL,'HALF_YEARLY','HALF_YEAR_GROSS'),
          ('TN','ANY',45000.01,60000,930,NULL,'HALF_YEARLY','HALF_YEAR_GROSS'),
          ('TN','ANY',60000.01,75000,1025,NULL,'HALF_YEARLY','HALF_YEAR_GROSS'),
          ('TN','ANY',75000.01,NULL,1250,NULL,'HALF_YEARLY','HALF_YEAR_GROSS')
        ) AS v(state_code, gender, min_amount, max_amount, amount, february_amount, frequency, basis)
        WHERE NOT EXISTS (
          SELECT 1 FROM pt_slab_rules p WHERE p.school_id IS NULL AND p.state_code = v.state_code
        );
        """
    )


def _policies() -> None:
    for table, role_sql in _ROLE_SQL.items():
        using_sql, check_sql = role_sql if isinstance(role_sql, tuple) else (role_sql, role_sql)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
        if table in _NULLABLE_SCHOOL:
            school_using = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR school_id IS NULL "
                "OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
            )
            school_check = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
            )
        else:
            school_using = (
                "current_setting('app.rls_bypass', true) = 'on' "
                "OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
            )
            school_check = school_using
        op.execute(
            f"""
            CREATE POLICY {table}_school ON {table}
            USING ({school_using})
            WITH CHECK ({school_check})
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_role ON {table} AS RESTRICTIVE
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR ({using_sql})
            )
            WITH CHECK (
              current_setting('app.rls_bypass', true) = 'on'
              OR ({check_sql})
            )
            """
        )
        op.execute(f"CREATE INDEX IF NOT EXISTS ix_{table}_school_id ON {table} (school_id)")


_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS app;"

_TABLES_SQL = r"""
CREATE TABLE IF NOT EXISTS payroll_school_settings (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL UNIQUE REFERENCES schools(id) ON DELETE CASCADE,
  pt_state VARCHAR(8) NOT NULL DEFAULT 'KA',
  tax_regime_default VARCHAR(8) NOT NULL DEFAULT 'NEW',
  pf_employee_rate NUMERIC(6,2) NOT NULL DEFAULT 12,
  pf_employer_rate NUMERIC(6,2) NOT NULL DEFAULT 12,
  pf_wage_ceiling NUMERIC(12,2) NOT NULL DEFAULT 15000,
  pf_default_mode VARCHAR(16) NOT NULL DEFAULT 'CAPPED',
  eps_rate NUMERIC(6,2) NOT NULL DEFAULT 8.33,
  esi_employee_rate NUMERIC(6,2) NOT NULL DEFAULT 0.75,
  esi_employer_rate NUMERIC(6,2) NOT NULL DEFAULT 3.25,
  esi_wage_ceiling NUMERIC(12,2) NOT NULL DEFAULT 21000,
  rounding_mode VARCHAR(16) NOT NULL DEFAULT 'NEAREST_RUPEE',
  lop_base VARCHAR(16) NOT NULL DEFAULT 'BASIC',
  pay_date_rule VARCHAR(32) NOT NULL DEFAULT 'LAST_WORKING_DAY',
  pay_day INTEGER,
  auto_publish BOOLEAN NOT NULL DEFAULT FALSE,
  bank_format VARCHAR(32) NOT NULL DEFAULT 'GENERIC_NEFT',
  template_code VARCHAR(32) NOT NULL DEFAULT 'CLASSIC',
  primary_color VARCHAR(7) NOT NULL DEFAULT '#1B4F72',
  accent_color VARCHAR(7) NOT NULL DEFAULT '#148F77',
  logo_object_key TEXT,
  show_employer_share BOOLEAN NOT NULL DEFAULT TRUE,
  show_lop BOOLEAN NOT NULL DEFAULT TRUE,
  show_bank_last4 BOOLEAN NOT NULL DEFAULT TRUE,
  show_pan BOOLEAN NOT NULL DEFAULT FALSE,
  shift_start TIME NOT NULL DEFAULT '09:00',
  shift_end TIME NOT NULL DEFAULT '16:00',
  grace_minutes INTEGER NOT NULL DEFAULT 10,
  full_day_minutes INTEGER NOT NULL DEFAULT 360,
  half_day_minutes INTEGER NOT NULL DEFAULT 180,
  geofence_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  geofence_lat NUMERIC(9,6),
  geofence_lng NUMERIC(9,6),
  geofence_radius_m INTEGER NOT NULL DEFAULT 200,
  bonus_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  bonus_percent NUMERIC(6,2) NOT NULL DEFAULT 8.33,
  bonus_wage_ceiling NUMERIC(12,2) NOT NULL DEFAULT 21000,
  bonus_calc_ceiling NUMERIC(12,2) NOT NULL DEFAULT 7000,
  bonus_month INTEGER NOT NULL DEFAULT 3,
  gratuity_cap NUMERIC(14,2) NOT NULL DEFAULT 2000000,
  gratuity_min_years NUMERIC(4,2) NOT NULL DEFAULT 5,
  require_different_approver BOOLEAN NOT NULL DEFAULT TRUE,
  effective_from DATE NOT NULL,
  updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT payroll_settings_rates CHECK (
    pf_employee_rate >= 0 AND pf_employer_rate >= 0 AND esi_employee_rate >= 0
    AND grace_minutes >= 0 AND half_day_minutes >= 0 AND full_day_minutes >= half_day_minutes
    AND bonus_month BETWEEN 1 AND 12 AND geofence_radius_m >= 0
  )
);

CREATE TABLE IF NOT EXISTS payroll_setting_revisions (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  effective_from DATE NOT NULL,
  snapshot JSONB NOT NULL,
  reason TEXT,
  changed_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_payroll_setting_revisions_school ON payroll_setting_revisions (school_id, effective_from);

CREATE TABLE IF NOT EXISTS salary_components (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  code VARCHAR(32) NOT NULL,
  name VARCHAR(120) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  calc_type VARCHAR(16) NOT NULL,
  amount NUMERIC(12,2) NOT NULL DEFAULT 0,
  percent NUMERIC(8,4),
  percent_of VARCHAR(32),
  formula VARCHAR(200),
  taxable BOOLEAN NOT NULL DEFAULT TRUE,
  pf_wage BOOLEAN NOT NULL DEFAULT FALSE,
  esi_wage BOOLEAN NOT NULL DEFAULT TRUE,
  prorate BOOLEAN NOT NULL DEFAULT TRUE,
  display_order INTEGER NOT NULL DEFAULT 0,
  effective_from DATE NOT NULL,
  effective_to DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT salary_component_kind CHECK (kind IN ('EARNING', 'DEDUCTION')),
  CONSTRAINT salary_component_calc CHECK (calc_type IN ('FIXED', 'PERCENT', 'FORMULA'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_salary_components_school_code_open
  ON salary_components (school_id, lower(code)) WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS salary_templates (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  name VARCHAR(120) NOT NULL,
  grade_code VARCHAR(32),
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_salary_templates_school_name
  ON salary_templates (school_id, lower(name));

CREATE TABLE IF NOT EXISTS salary_template_lines (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  template_id UUID NOT NULL REFERENCES salary_templates(id) ON DELETE CASCADE,
  component_id UUID NOT NULL REFERENCES salary_components(id) ON DELETE CASCADE,
  amount NUMERIC(12,2),
  percent NUMERIC(8,4),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (template_id, component_id)
);

CREATE TABLE IF NOT EXISTS employee_salary_assignments (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  template_id UUID NOT NULL REFERENCES salary_templates(id) ON DELETE RESTRICT,
  effective_from DATE NOT NULL,
  effective_to DATE,
  overrides JSONB NOT NULL DEFAULT '{}'::jsonb,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_salary_assignment_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_salary_assignment_employee_from
  ON employee_salary_assignments (employee_id, effective_from);

CREATE TABLE IF NOT EXISTS employee_statutory_profiles (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  pan VARCHAR(10),
  uan VARCHAR(32),
  pf_number VARCHAR(32),
  esi_number VARCHAR(32),
  gender VARCHAR(8) NOT NULL DEFAULT 'OTHER',
  tax_regime VARCHAR(8),
  metro BOOLEAN NOT NULL DEFAULT FALSE,
  pt_state VARCHAR(8),
  pf_enrolled BOOLEAN NOT NULL DEFAULT FALSE,
  pf_wage_mode VARCHAR(16),
  pf_employee_rate NUMERIC(6,2),
  pf_employer_rate NUMERIC(6,2),
  pf_effective_from DATE,
  pf_effective_to DATE,
  vpf_rate NUMERIC(6,2),
  vpf_amount NUMERIC(12,2),
  esi_enrolled BOOLEAN NOT NULL DEFAULT FALSE,
  esi_effective_from DATE,
  esi_effective_to DATE,
  branch_name VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (employee_id),
  CONSTRAINT fk_statutory_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE,
  CONSTRAINT statutory_gender CHECK (gender IN ('MALE', 'FEMALE', 'OTHER'))
);

CREATE TABLE IF NOT EXISTS employee_bank_accounts (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  account_holder VARCHAR(120) NOT NULL,
  account_number VARCHAR(18) NOT NULL,
  ifsc VARCHAR(11) NOT NULL,
  bank_name VARCHAR(120) NOT NULL,
  is_primary BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_bank_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_employee_bank_primary
  ON employee_bank_accounts (employee_id) WHERE is_primary;

CREATE TABLE IF NOT EXISTS school_holidays (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  holiday_date DATE NOT NULL,
  name VARCHAR(160) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  branch_name VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT holiday_kind CHECK (kind IN ('NATIONAL', 'STATE', 'SCHOOL', 'OPTIONAL', 'RESTRICTED'))
);
CREATE INDEX IF NOT EXISTS ix_school_holidays_school_day ON school_holidays (school_id, holiday_date);

CREATE TABLE IF NOT EXISTS weekly_off_rules (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  weekday INTEGER NOT NULL,
  occurrence VARCHAR(8) NOT NULL DEFAULT 'ALL',
  branch_name VARCHAR(120) NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (school_id, weekday, occurrence, branch_name),
  CONSTRAINT weekly_off_day CHECK (weekday BETWEEN 0 AND 6),
  CONSTRAINT weekly_off_occ CHECK (occurrence IN ('ALL', '1', '2', '3', '4', '5', 'LAST'))
);

CREATE TABLE IF NOT EXISTS pt_slab_rules (
  id UUID PRIMARY KEY,
  school_id UUID REFERENCES schools(id) ON DELETE CASCADE,
  state_code VARCHAR(8) NOT NULL,
  gender VARCHAR(8) NOT NULL DEFAULT 'ANY',
  min_amount NUMERIC(12,2) NOT NULL,
  max_amount NUMERIC(12,2),
  amount NUMERIC(12,2) NOT NULL,
  february_amount NUMERIC(12,2),
  frequency VARCHAR(16) NOT NULL DEFAULT 'MONTHLY',
  basis VARCHAR(24) NOT NULL DEFAULT 'MONTHLY_GROSS',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_pt_slab_rules_school ON pt_slab_rules (school_id, state_code);

CREATE TABLE IF NOT EXISTS payroll_loans (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  kind VARCHAR(16) NOT NULL DEFAULT 'LOAN',
  principal NUMERIC(12,2) NOT NULL,
  balance NUMERIC(12,2) NOT NULL,
  emi NUMERIC(12,2) NOT NULL,
  annual_rate NUMERIC(6,2) NOT NULL DEFAULT 0,
  start_date DATE NOT NULL,
  paused_until DATE,
  status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_loan_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE,
  CONSTRAINT loan_amounts CHECK (principal >= 0 AND balance >= 0 AND emi >= 0),
  CONSTRAINT loan_kind CHECK (kind IN ('LOAN', 'ADVANCE'))
);

CREATE TABLE IF NOT EXISTS payroll_arrears (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  reason TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  payslip_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_arrear_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payroll_reimbursements (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  note TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  payslip_id UUID,
  decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_reimburse_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tax_declarations (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  financial_year VARCHAR(9) NOT NULL,
  regime VARCHAR(8) NOT NULL DEFAULT 'NEW',
  section_80c NUMERIC(12,2) NOT NULL DEFAULT 0,
  section_80d NUMERIC(12,2) NOT NULL DEFAULT 0,
  other_chapter_vi NUMERIC(12,2) NOT NULL DEFAULT 0,
  annual_rent NUMERIC(12,2) NOT NULL DEFAULT 0,
  status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (employee_id, financial_year),
  CONSTRAINT fk_declaration_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tax_declaration_proofs (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  declaration_id UUID NOT NULL REFERENCES tax_declarations(id) ON DELETE CASCADE,
  section VARCHAR(32) NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  object_key TEXT,
  note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payroll_payslip_lines (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  payslip_id UUID NOT NULL REFERENCES payroll_payslips(id) ON DELETE CASCADE,
  code VARCHAR(32) NOT NULL,
  name VARCHAR(120) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  amount NUMERIC(12,2) NOT NULL,
  display_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_payslip_lines_payslip ON payroll_payslip_lines (payslip_id);

CREATE TABLE IF NOT EXISTS payroll_payments (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  payslip_id UUID NOT NULL REFERENCES payroll_payslips(id) ON DELETE CASCADE,
  amount NUMERIC(12,2) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  utr VARCHAR(64),
  exported_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  failure_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT payment_status CHECK (status IN ('PENDING', 'EXPORTED', 'PAID', 'FAILED'))
);

CREATE TABLE IF NOT EXISTS payroll_audit_logs (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
  action VARCHAR(64) NOT NULL,
  entity_type VARCHAR(64) NOT NULL,
  entity_id UUID,
  reason TEXT,
  before JSONB,
  after JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_payroll_audit_school_created ON payroll_audit_logs (school_id, created_at);

CREATE TABLE IF NOT EXISTS payroll_settlements (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  payroll_run_id UUID REFERENCES payroll_runs(id) ON DELETE SET NULL,
  last_working_day DATE NOT NULL,
  gratuity NUMERIC(14,2) NOT NULL DEFAULT 0,
  leave_encashment NUMERIC(12,2) NOT NULL DEFAULT 0,
  notice_recovery NUMERIC(12,2) NOT NULL DEFAULT 0,
  net NUMERIC(14,2) NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'PENDING_APPROVAL',
  detail JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_settlement_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payroll_compliance_events (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  title VARCHAR(160) NOT NULL,
  due_on DATE NOT NULL,
  kind VARCHAR(32) NOT NULL,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS payroll_role_grants (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role VARCHAR(32) NOT NULL,
  effective_from DATE NOT NULL,
  effective_to DATE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (school_id, user_id, role),
  CONSTRAINT payroll_grant_role CHECK (
    role IN ('PAYROLL_ADMIN', 'HR_MANAGER', 'ACCOUNTANT', 'PAYROLL_APPROVER', 'AUDITOR')
  )
);

CREATE TABLE IF NOT EXISTS staff_attendance_sessions (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  attendance_date DATE NOT NULL,
  check_in_at TIMESTAMPTZ NOT NULL,
  check_out_at TIMESTAMPTZ,
  server_recorded_at TIMESTAMPTZ NOT NULL,
  check_in_lat NUMERIC(9,6),
  check_in_lng NUMERIC(9,6),
  check_out_lat NUMERIC(9,6),
  check_out_lng NUMERIC(9,6),
  source VARCHAR(16) NOT NULL DEFAULT 'SELF',
  geofence_status VARCHAR(16) NOT NULL DEFAULT 'DISABLED',
  manual_reason TEXT,
  is_late BOOLEAN NOT NULL DEFAULT FALSE,
  left_early BOOLEAN NOT NULL DEFAULT FALSE,
  derived_status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
  flagged BOOLEAN NOT NULL DEFAULT FALSE,
  corrected_by UUID REFERENCES users(id) ON DELETE SET NULL,
  correction_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_staff_session_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_staff_session_school_day ON staff_attendance_sessions (school_id, attendance_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_staff_session_open
  ON staff_attendance_sessions (employee_id) WHERE check_out_at IS NULL;

CREATE TABLE IF NOT EXISTS staff_attendance_regularisations (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  employee_id UUID NOT NULL,
  attendance_date DATE NOT NULL,
  requested_check_in TIMESTAMPTZ NOT NULL,
  requested_check_out TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
  decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
  decided_at TIMESTAMPTZ,
  decision_note TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_regularisation_employee_school
    FOREIGN KEY (employee_id, school_id) REFERENCES employees (id, school_id) ON DELETE CASCADE,
  CONSTRAINT regularisation_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED'))
);

CREATE TABLE IF NOT EXISTS staff_attendance_period_locks (
  id UUID PRIMARY KEY,
  school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
  period_start DATE NOT NULL,
  period_end DATE NOT NULL,
  locked_by UUID REFERENCES users(id) ON DELETE SET NULL,
  payroll_run_id UUID REFERENCES payroll_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (school_id, period_start, period_end),
  CONSTRAINT attendance_lock_order CHECK (period_end >= period_start)
);
"""
