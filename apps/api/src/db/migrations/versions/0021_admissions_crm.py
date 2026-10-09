"""Phase 13 admissions: enquiries/applications/status history/documents.

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-04
"""

from typing import Sequence, Union

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_enquiries (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            student_name VARCHAR(200) NOT NULL,
            student_dob DATE,
            desired_grade_id UUID REFERENCES grades(id) ON DELETE SET NULL,
            parent_name VARCHAR(200),
            phone VARCHAR(32),
            email VARCHAR(255),
            notes TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'ENQUIRY',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_enquiries_school_id ON admission_enquiries (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_enquiries_desired_grade_id ON admission_enquiries (desired_grade_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_enquiries_status ON admission_enquiries (status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_applications (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            enquiry_id UUID REFERENCES admission_enquiries(id) ON DELETE SET NULL,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            first_name VARCHAR(100) NOT NULL,
            last_name VARCHAR(100) NOT NULL,
            email VARCHAR(255) NOT NULL,
            phone VARCHAR(32),
            date_of_birth DATE,
            desired_grade_id UUID REFERENCES grades(id) ON DELETE SET NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'APPLIED',
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_applications_school_id ON admission_applications (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_applications_enquiry_id ON admission_applications (enquiry_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admission_applications_desired_grade_id ON admission_applications (desired_grade_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_applications_status ON admission_applications (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_applications_submitted_at ON admission_applications (submitted_at)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_status_history (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            application_id UUID NOT NULL REFERENCES admission_applications(id) ON DELETE CASCADE,
            changed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            from_status VARCHAR(32),
            to_status VARCHAR(32) NOT NULL,
            note TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_status_history_school_id ON admission_status_history (school_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_admission_status_history_application_id ON admission_status_history (application_id)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_status_history_to_status ON admission_status_history (to_status)")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS admission_documents (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            application_id UUID NOT NULL REFERENCES admission_applications(id) ON DELETE CASCADE,
            kind VARCHAR(64) NOT NULL DEFAULT 'OTHER',
            document_url TEXT,
            meta_json TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_documents_school_id ON admission_documents (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_documents_application_id ON admission_documents (application_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_admission_documents_kind ON admission_documents (kind)")


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS admission_documents CASCADE")
    op.execute("DROP TABLE IF EXISTS admission_status_history CASCADE")
    op.execute("DROP TABLE IF EXISTS admission_applications CASCADE")
    op.execute("DROP TABLE IF EXISTS admission_enquiries CASCADE")

