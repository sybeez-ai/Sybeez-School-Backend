"""HRM Phase 6: HR letters and employee documents.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0032"
down_revision: Union[str, None] = "0031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_letters (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            employee_id UUID NOT NULL,
            letter_type VARCHAR(16) NOT NULL,
            title VARCHAR(200) NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            status VARCHAR(16) NOT NULL DEFAULT 'DRAFT',
            reference_number VARCHAR(64),
            issue_date DATE,
            issued_at TIMESTAMPTZ,
            issued_by UUID REFERENCES users(id) ON DELETE SET NULL,
            revoked_at TIMESTAMPTZ,
            revoked_by UUID REFERENCES users(id) ON DELETE SET NULL,
            revoke_reason TEXT,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_hr_letters_employee_same_school FOREIGN KEY (employee_id, school_id)
                REFERENCES employees (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_hr_letters_type_allowed
                CHECK (letter_type IN ('OFFER', 'APPOINTMENT', 'EXPERIENCE', 'RELIEVING', 'OTHER')),
            CONSTRAINT ck_hr_letters_status_allowed CHECK (status IN ('DRAFT', 'ISSUED', 'REVOKED')),
            CONSTRAINT ck_hr_letters_issued_has_date
                CHECK (status = 'DRAFT' OR (issue_date IS NOT NULL AND issued_at IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_letters_school_reference "
        "ON hr_letters (school_id, reference_number) WHERE reference_number IS NOT NULL"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_hr_letters_school_employee ON hr_letters (school_id, employee_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_hr_letters_school_status ON hr_letters (school_id, status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS employee_documents (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            employee_id UUID NOT NULL,
            category VARCHAR(32) NOT NULL,
            title VARCHAR(200) NOT NULL,
            notes TEXT,
            status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
            shared_with_employee BOOLEAN NOT NULL DEFAULT FALSE,
            file_name VARCHAR(255) NOT NULL,
            mime_type VARCHAR(128),
            file_size INTEGER NOT NULL,
            storage_key TEXT NOT NULL,
            uploaded_by UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_employee_documents_employee_same_school FOREIGN KEY (employee_id, school_id)
                REFERENCES employees (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_employee_documents_category_allowed
                CHECK (category IN ('ID_PROOF', 'CONTRACT', 'QUALIFICATION', 'MEDICAL', 'BANK', 'OTHER')),
            CONSTRAINT ck_employee_documents_status_allowed CHECK (status IN ('ACTIVE', 'ARCHIVED')),
            CONSTRAINT ck_employee_documents_file_size_positive CHECK (file_size > 0),
            CONSTRAINT uq_employee_documents_storage_key UNIQUE (storage_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_employee_documents_school_employee "
        "ON employee_documents (school_id, employee_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS employee_documents")
    op.execute("DROP TABLE IF EXISTS hr_letters")
