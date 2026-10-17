"""Finance production: Indian FY sequences, ledger, concessions, RLS.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FEE_RLS_TABLES = [
    "fee_categories",
    "fee_structures",
    "fee_structure_items",
    "fee_installments",
    "student_fee_accounts",
    "fee_adjustments",
    "fee_invoices",
    "fee_invoice_items",
    "fee_payments",
    "fee_payment_allocations",
    "fee_payment_refunds",
    "fee_receipts",
    "late_fee_rules",
    "fee_settings",
    "fee_sequences",
    "fee_audit_logs",
    "fee_reminder_logs",
    "school_branches",
    "financial_years",
    "sibling_groups",
    "student_fee_profiles",
    "finance_staff_scopes",
    "fee_concession_rules",
    "fee_concession_requests",
    "fee_webhook_events",
    "fee_payment_tenders",
    "fee_ledger_entries",
    "fee_day_closes",
    "finance_approvals",
    "fee_fine_waiver_requests",
]


def upgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE fee_categories ADD COLUMN IF NOT EXISTS is_80c_eligible BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE fee_categories ADD COLUMN IF NOT EXISTS gst_rate NUMERIC(6,2) NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fee_categories ADD COLUMN IF NOT EXISTS tally_ledger VARCHAR(128)")

    op.execute("ALTER TABLE fee_payments ADD COLUMN IF NOT EXISTS branch_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_payments_branch_id ON fee_payments (branch_id)")

    op.execute("ALTER TABLE fee_payment_refunds ADD COLUMN IF NOT EXISTS approved_by UUID")
    op.execute("ALTER TABLE fee_payment_refunds ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255)")
    op.execute("ALTER TABLE fee_payment_refunds ADD COLUMN IF NOT EXISTS decision_note TEXT")

    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE'")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS cancel_reason TEXT")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS cancelled_by UUID")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS reprint_count INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS fy_label VARCHAR(16)")
    op.execute("ALTER TABLE fee_receipts ADD COLUMN IF NOT EXISTS branch_id UUID")

    op.execute("ALTER TABLE late_fee_rules ADD COLUMN IF NOT EXISTS slabs_json TEXT")
    op.execute("ALTER TABLE fee_audit_logs ADD COLUMN IF NOT EXISTS ip_address VARCHAR(64)")

    op.execute("ALTER TABLE fee_sequences ADD COLUMN IF NOT EXISTS fy_label VARCHAR(16)")
    op.execute("UPDATE fee_sequences SET fy_label = year::text WHERE fy_label IS NULL OR fy_label = ''")
    op.execute("ALTER TABLE fee_sequences ALTER COLUMN fy_label SET NOT NULL")
    op.execute("ALTER TABLE fee_sequences DROP CONSTRAINT IF EXISTS uq_fee_sequence")
    op.execute(
        """
        DO $$ BEGIN
          ALTER TABLE fee_sequences
            ADD CONSTRAINT uq_fee_sequence_fy UNIQUE (school_id, seq_type, fy_label);
        EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
        END $$
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_payments_gateway_order
          ON fee_payments (gateway_order_id) WHERE gateway_order_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_payments_gateway_payment
          ON fee_payments (gateway_payment_id) WHERE gateway_payment_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_payments_idempotency
          ON fee_payments (school_id, idempotency_key) WHERE idempotency_key IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_alloc_payment_invoice
          ON fee_payment_allocations (payment_id, invoice_id)
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_refund_idempotency
          ON fee_payment_refunds (school_id, idempotency_key) WHERE idempotency_key IS NOT NULL
        """
    )

    # Composite keys so a child row cannot point at another school's parent.
    for table in (
        "fee_payments",
        "fee_invoices",
        "fee_receipts",
        "fee_categories",
        "student_fee_accounts",
        "students",
        "fee_payment_refunds",
    ):
        op.execute(
            f"""
            DO $$ BEGIN
              ALTER TABLE {table}
                ADD CONSTRAINT uq_{table}_school_pk UNIQUE (school_id, id);
            EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
            END $$
            """
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS school_branches (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            code VARCHAR(32) NOT NULL,
            address TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_school_branch_code UNIQUE (school_id, code),
            CONSTRAINT uq_school_branches_school_pk UNIQUE (school_id, id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_school_branches_school_id ON school_branches (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_years (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            label VARCHAR(16) NOT NULL,
            start_date DATE NOT NULL,
            end_date DATE NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            is_current BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_financial_year_label UNIQUE (school_id, label),
            CONSTRAINT uq_financial_years_school_pk UNIQUE (school_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS sibling_groups (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_sibling_groups_school_pk UNIQUE (school_id, id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS student_fee_profiles (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL,
            branch_id UUID,
            category VARCHAR(32) NOT NULL DEFAULT 'GENERAL',
            sibling_group_id UUID,
            transport_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
            hostel_opt_in BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_student_fee_profile UNIQUE (school_id, student_id),
            CONSTRAINT fk_profile_student_school FOREIGN KEY (school_id, student_id)
                REFERENCES students (school_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_profile_branch_school FOREIGN KEY (school_id, branch_id)
                REFERENCES school_branches (school_id, id) ON DELETE SET NULL,
            CONSTRAINT fk_profile_sibling_school FOREIGN KEY (school_id, sibling_group_id)
                REFERENCES sibling_groups (school_id, id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_student_fee_profiles_school_id ON student_fee_profiles (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_staff_scopes (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            branch_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_finance_staff_scope UNIQUE (school_id, user_id, branch_id),
            CONSTRAINT fk_staff_scope_branch FOREIGN KEY (school_id, branch_id)
                REFERENCES school_branches (school_id, id) ON DELETE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_concession_rules (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            code VARCHAR(64) NOT NULL,
            rule_type VARCHAR(32) NOT NULL,
            value_type VARCHAR(16) NOT NULL,
            value NUMERIC(12,2) NOT NULL,
            fee_category_id UUID,
            student_category VARCHAR(32),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_concession_rule_code UNIQUE (school_id, code),
            CONSTRAINT fk_concession_rule_category FOREIGN KEY (school_id, fee_category_id)
                REFERENCES fee_categories (school_id, id) ON DELETE SET NULL
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_concession_requests (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL,
            rule_id UUID,
            fee_category_id UUID,
            value_type VARCHAR(16) NOT NULL,
            value NUMERIC(12,2) NOT NULL,
            computed_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            reason TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            requested_by UUID,
            approved_by UUID,
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_concession_req_student FOREIGN KEY (school_id, student_id)
                REFERENCES students (school_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_concession_requests_school ON fee_concession_requests (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_webhook_events (
            id UUID PRIMARY KEY,
            school_id UUID,
            provider VARCHAR(32) NOT NULL,
            event_id VARCHAR(128) NOT NULL,
            event_type VARCHAR(64) NOT NULL DEFAULT '',
            order_id VARCHAR(128),
            gateway_payment_id VARCHAR(128),
            credited BOOLEAN NOT NULL DEFAULT FALSE,
            outcome VARCHAR(64) NOT NULL DEFAULT '',
            payload TEXT,
            processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_webhook_event_id UNIQUE (event_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_webhook_events_school ON fee_webhook_events (school_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_payment_tenders (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL,
            payment_id UUID NOT NULL,
            receipt_id UUID,
            branch_id UUID,
            method VARCHAR(32) NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            instrument_no VARCHAR(64),
            bank_name VARCHAR(128),
            instrument_date DATE,
            reference VARCHAR(128),
            status VARCHAR(32) NOT NULL DEFAULT 'SETTLED',
            deposited_at TIMESTAMPTZ,
            cleared_at TIMESTAMPTZ,
            bounced_at TIMESTAMPTZ,
            bounce_reason TEXT,
            bounce_charge NUMERIC(12,2) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_tender_payment_school FOREIGN KEY (school_id, payment_id)
                REFERENCES fee_payments (school_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_tender_receipt_school FOREIGN KEY (school_id, receipt_id)
                REFERENCES fee_receipts (school_id, id) ON DELETE SET NULL,
            CONSTRAINT fk_tender_branch_school FOREIGN KEY (school_id, branch_id)
                REFERENCES school_branches (school_id, id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_payment_tenders_school ON fee_payment_tenders (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_payment_tenders_payment ON fee_payment_tenders (payment_id)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_ledger_entries (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL,
            account_id UUID,
            branch_id UUID,
            entry_date DATE NOT NULL,
            entry_type VARCHAR(32) NOT NULL,
            debit NUMERIC(12,2) NOT NULL DEFAULT 0,
            credit NUMERIC(12,2) NOT NULL DEFAULT 0,
            ref_type VARCHAR(64) NOT NULL,
            ref_id UUID,
            narration TEXT NOT NULL DEFAULT '',
            actor_user_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_ledger_student_school FOREIGN KEY (school_id, student_id)
                REFERENCES students (school_id, id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_fee_ledger_school_student ON fee_ledger_entries (school_id, student_id)")
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_ledger_ref
          ON fee_ledger_entries (school_id, entry_type, ref_type, ref_id)
          WHERE ref_id IS NOT NULL
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_day_closes (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            branch_id UUID,
            cashier_user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            business_date DATE NOT NULL,
            opening_cash NUMERIC(12,2) NOT NULL DEFAULT 0,
            physical_cash NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_cash NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_cheque NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_upi NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_card NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_neft NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_online NUMERIC(12,2) NOT NULL DEFAULT 0,
            system_total NUMERIC(12,2) NOT NULL DEFAULT 0,
            variance NUMERIC(12,2) NOT NULL DEFAULT 0,
            notes TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'SUBMITTED',
            verified_by UUID,
            verified_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_day_close_branch FOREIGN KEY (school_id, branch_id)
                REFERENCES school_branches (school_id, id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_day_close_cashier_day
          ON fee_day_closes (
            school_id, cashier_user_id, business_date,
            COALESCE(branch_id, '00000000-0000-0000-0000-000000000000'::uuid)
          )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS finance_approvals (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            entity_type VARCHAR(32) NOT NULL,
            entity_id UUID NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            reason TEXT NOT NULL,
            decision_note TEXT,
            payload TEXT,
            requested_by UUID,
            approved_by UUID,
            decided_at TIMESTAMPTZ,
            branch_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_finance_approvals_school ON finance_approvals (school_id, status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS fee_fine_waiver_requests (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            invoice_id UUID NOT NULL,
            student_id UUID NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            requested_by UUID,
            approved_by UUID,
            decided_at TIMESTAMPTZ,
            decision_note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_waiver_invoice_school FOREIGN KEY (school_id, invoice_id)
                REFERENCES fee_invoices (school_id, id) ON DELETE CASCADE
        )
        """
    )

    # Constraints that live inside CREATE TABLE must also be applied when
    # metadata.create_all already built the table without the composite key.
    def _ensure(statement: str) -> None:
        op.execute(
            f"""
            DO $$ BEGIN
              {statement};
            EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL;
            END $$
            """
        )

    for statement in (
        "ALTER TABLE school_branches ADD CONSTRAINT uq_school_branches_school_pk UNIQUE (school_id, id)",
        "ALTER TABLE financial_years ADD CONSTRAINT uq_financial_years_school_pk UNIQUE (school_id, id)",
        "ALTER TABLE sibling_groups ADD CONSTRAINT uq_sibling_groups_school_pk UNIQUE (school_id, id)",
        """ALTER TABLE student_fee_profiles ADD CONSTRAINT fk_profile_student_school
           FOREIGN KEY (school_id, student_id) REFERENCES students (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE student_fee_profiles ADD CONSTRAINT fk_profile_branch_school
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE student_fee_profiles ADD CONSTRAINT fk_profile_sibling_school
           FOREIGN KEY (school_id, sibling_group_id) REFERENCES sibling_groups (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE finance_staff_scopes ADD CONSTRAINT fk_staff_scope_branch
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE fee_concession_rules ADD CONSTRAINT fk_concession_rule_category
           FOREIGN KEY (school_id, fee_category_id) REFERENCES fee_categories (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE fee_concession_requests ADD CONSTRAINT fk_concession_req_student
           FOREIGN KEY (school_id, student_id) REFERENCES students (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE fee_payment_tenders ADD CONSTRAINT fk_tender_payment_school
           FOREIGN KEY (school_id, payment_id) REFERENCES fee_payments (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE fee_payment_tenders ADD CONSTRAINT fk_tender_receipt_school
           FOREIGN KEY (school_id, receipt_id) REFERENCES fee_receipts (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE fee_payment_tenders ADD CONSTRAINT fk_tender_branch_school
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE fee_ledger_entries ADD CONSTRAINT fk_ledger_student_school
           FOREIGN KEY (school_id, student_id) REFERENCES students (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE fee_day_closes ADD CONSTRAINT fk_day_close_branch
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE fee_fine_waiver_requests ADD CONSTRAINT fk_waiver_invoice_school
           FOREIGN KEY (school_id, invoice_id) REFERENCES fee_invoices (school_id, id) ON DELETE CASCADE""",
        """ALTER TABLE fee_payments ADD CONSTRAINT fk_fee_payments_branch_school
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE SET NULL""",
        """ALTER TABLE fee_receipts ADD CONSTRAINT fk_fee_receipts_branch_school
           FOREIGN KEY (school_id, branch_id) REFERENCES school_branches (school_id, id) ON DELETE SET NULL""",
    ):
        _ensure(statement)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fee_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("fee_ledger_entries", "fee_audit_logs"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fee_append_only()
            """
        )

    # Non-superuser role so FORCE RLS actually applies (owners/superusers bypass it).
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sybeez_app') THEN
            CREATE ROLE sybeez_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
          END IF;
        END $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO sybeez_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO sybeez_app"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO sybeez_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO sybeez_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO sybeez_app"
    )

    for table in FEE_RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant ON {table}
            USING (
              current_setting('app.rls_bypass', true) = 'on'
              OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            WITH CHECK (
              school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
            )
            """
        )


def downgrade() -> None:
    from alembic import op

    for table in reversed(FEE_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS fee_ledger_entries_append_only ON fee_ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS fee_audit_logs_append_only ON fee_audit_logs")
    op.execute("DROP FUNCTION IF EXISTS fee_append_only()")
    for table in (
        "fee_fine_waiver_requests",
        "finance_approvals",
        "fee_day_closes",
        "fee_ledger_entries",
        "fee_payment_tenders",
        "fee_webhook_events",
        "fee_concession_requests",
        "fee_concession_rules",
        "finance_staff_scopes",
        "student_fee_profiles",
        "sibling_groups",
        "financial_years",
        "school_branches",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
