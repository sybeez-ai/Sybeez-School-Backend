"""Phase 10 fees & payments.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-27
"""

from typing import Sequence, Union

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_categories (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            code VARCHAR(64) NOT NULL,
            description TEXT,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_category_school_code UNIQUE (school_id, code)
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_categories_school_id ON fee_categories (school_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_structures (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            academic_year_id UUID NOT NULL REFERENCES academic_years(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            grade_id UUID REFERENCES grades(id) ON DELETE SET NULL,
            section_id UUID REFERENCES classes(id) ON DELETE SET NULL,
            charge_model VARCHAR(32) NOT NULL DEFAULT 'YEARLY',
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            effective_from DATE,
            effective_to DATE,
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_structures_school_id ON fee_structures (school_id);
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_structures_academic_year_id ON fee_structures (academic_year_id);
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_structures_status ON fee_structures (status);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_structure_items (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            fee_structure_id UUID NOT NULL REFERENCES fee_structures(id) ON DELETE CASCADE,
            fee_category_id UUID NOT NULL REFERENCES fee_categories(id) ON DELETE RESTRICT,
            amount NUMERIC(12,2) NOT NULL,
            taxable BOOLEAN NOT NULL DEFAULT FALSE,
            refundable BOOLEAN NOT NULL DEFAULT FALSE,
            mandatory BOOLEAN NOT NULL DEFAULT TRUE,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_structure_items_structure ON fee_structure_items (fee_structure_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_installments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            fee_structure_id UUID NOT NULL REFERENCES fee_structures(id) ON DELETE CASCADE,
            name VARCHAR(128) NOT NULL,
            due_date DATE NOT NULL,
            sequence INTEGER NOT NULL DEFAULT 1,
            amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_installments_structure ON fee_installments (fee_structure_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS student_fee_accounts (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
            academic_year_id UUID NOT NULL REFERENCES academic_years(id) ON DELETE CASCADE,
            fee_structure_id UUID REFERENCES fee_structures(id) ON DELETE SET NULL,
            enrollment_id UUID REFERENCES enrollments(id) ON DELETE SET NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_student_fee_account_year UNIQUE (school_id, student_id, academic_year_id)
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_student_fee_accounts_student ON student_fee_accounts (student_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_adjustments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_fee_account_id UUID NOT NULL REFERENCES student_fee_accounts(id) ON DELETE CASCADE,
            fee_category_id UUID REFERENCES fee_categories(id) ON DELETE SET NULL,
            type VARCHAR(32) NOT NULL DEFAULT 'DISCOUNT',
            amount NUMERIC(12,2) NOT NULL,
            reason TEXT NOT NULL,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_adjustments_account ON fee_adjustments (student_fee_account_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_invoices (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_fee_account_id UUID NOT NULL REFERENCES student_fee_accounts(id) ON DELETE CASCADE,
            installment_id UUID REFERENCES fee_installments(id) ON DELETE SET NULL,
            invoice_number VARCHAR(64) NOT NULL,
            invoice_date DATE NOT NULL,
            due_date DATE NOT NULL,
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            subtotal NUMERIC(12,2) NOT NULL DEFAULT 0,
            discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            tax_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            total_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            paid_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            balance_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'DRAFT',
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            cancelled_at TIMESTAMPTZ,
            cancel_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_invoice_number UNIQUE (school_id, invoice_number)
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_invoices_account ON fee_invoices (student_fee_account_id);
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_invoices_status ON fee_invoices (status);
    """)

    op.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_invoice_account_installment
            ON fee_invoices (student_fee_account_id, installment_id)
            WHERE installment_id IS NOT NULL;
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_invoice_items (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            invoice_id UUID NOT NULL REFERENCES fee_invoices(id) ON DELETE CASCADE,
            fee_category_id UUID REFERENCES fee_categories(id) ON DELETE SET NULL,
            description VARCHAR(255) NOT NULL,
            quantity NUMERIC(12,2) NOT NULL DEFAULT 1,
            unit_amount NUMERIC(12,2) NOT NULL,
            discount_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            tax_amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            total_amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_invoice_items_invoice ON fee_invoice_items (invoice_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_payments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            student_fee_account_id UUID NOT NULL REFERENCES student_fee_accounts(id) ON DELETE CASCADE,
            invoice_id UUID REFERENCES fee_invoices(id) ON DELETE SET NULL,
            payment_number VARCHAR(64) NOT NULL,
            amount NUMERIC(12,2) NOT NULL,
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            method VARCHAR(32) NOT NULL DEFAULT 'ONLINE',
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            gateway VARCHAR(64),
            gateway_order_id VARCHAR(128),
            gateway_payment_id VARCHAR(128),
            gateway_reference VARCHAR(255),
            paid_at TIMESTAMPTZ,
            received_by UUID REFERENCES users(id) ON DELETE SET NULL,
            notes TEXT,
            idempotency_key VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_payment_number UNIQUE (school_id, payment_number)
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_payments_status ON fee_payments (status);
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_payments_gateway_order ON fee_payments (gateway_order_id);
    """)

    op.execute("""
CREATE UNIQUE INDEX IF NOT EXISTS uq_fee_payment_idempotency
            ON fee_payments (school_id, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_payment_allocations (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            payment_id UUID NOT NULL REFERENCES fee_payments(id) ON DELETE CASCADE,
            invoice_id UUID NOT NULL REFERENCES fee_invoices(id) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_payment_allocations_payment ON fee_payment_allocations (payment_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_payment_refunds (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            payment_id UUID NOT NULL REFERENCES fee_payments(id) ON DELETE CASCADE,
            amount NUMERIC(12,2) NOT NULL,
            reason TEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
            gateway_refund_id VARCHAR(128),
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_receipts (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            payment_id UUID NOT NULL REFERENCES fee_payments(id) ON DELETE CASCADE,
            receipt_number VARCHAR(64) NOT NULL,
            issued_at TIMESTAMPTZ NOT NULL,
            generated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            pdf_path VARCHAR(512),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_receipt_number UNIQUE (school_id, receipt_number)
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_receipts_payment ON fee_receipts (payment_id);
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS late_fee_rules (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            fee_category_id UUID REFERENCES fee_categories(id) ON DELETE SET NULL,
            grace_days INTEGER NOT NULL DEFAULT 0,
            calculation_type VARCHAR(32) NOT NULL DEFAULT 'FIXED',
            amount NUMERIC(12,2) NOT NULL DEFAULT 0,
            maximum_amount NUMERIC(12,2),
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_settings (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            currency VARCHAR(8) NOT NULL DEFAULT 'INR',
            allow_student_view_fees BOOLEAN NOT NULL DEFAULT FALSE,
            allow_partial_payments BOOLEAN NOT NULL DEFAULT TRUE,
            gateway_provider VARCHAR(64) NOT NULL DEFAULT 'mock',
            gateway_mode VARCHAR(16) NOT NULL DEFAULT 'TEST',
            gateway_public_key VARCHAR(255),
            invoice_prefix VARCHAR(16) NOT NULL DEFAULT 'INV',
            receipt_prefix VARCHAR(16) NOT NULL DEFAULT 'REC',
            payment_prefix VARCHAR(16) NOT NULL DEFAULT 'PAY',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_settings_school UNIQUE (school_id)
        );
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_sequences (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            seq_type VARCHAR(32) NOT NULL,
            year INTEGER NOT NULL,
            last_value INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_fee_sequence UNIQUE (school_id, seq_type, year)
        );
    """)

    op.execute("""
CREATE TABLE IF NOT EXISTS fee_audit_logs (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            action VARCHAR(64) NOT NULL,
            entity_type VARCHAR(64) NOT NULL,
            entity_id UUID,
            old_values TEXT,
            new_values TEXT,
            reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
CREATE INDEX IF NOT EXISTS ix_fee_audit_logs_school ON fee_audit_logs (school_id);
    """)



def downgrade() -> None:
    from alembic import op

    for t in (
        "fee_audit_logs",
        "fee_sequences",
        "fee_settings",
        "late_fee_rules",
        "fee_receipts",
        "fee_payment_refunds",
        "fee_payment_allocations",
        "fee_payments",
        "fee_invoice_items",
        "fee_invoices",
        "fee_adjustments",
        "student_fee_accounts",
        "fee_installments",
        "fee_structure_items",
        "fee_structures",
        "fee_categories",
    ):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
