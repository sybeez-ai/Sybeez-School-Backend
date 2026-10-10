"""HRM Phase 8: employee self-service HR requests with an event history.

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0034"
down_revision: Union[str, None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_requests (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            employee_id UUID NOT NULL,
            request_type VARCHAR(32) NOT NULL,
            subject VARCHAR(200) NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            requested_changes JSONB,
            status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            source VARCHAR(16) NOT NULL DEFAULT 'EMPLOYEE',
            submitted_by UUID REFERENCES users(id) ON DELETE SET NULL,
            decision_note TEXT,
            decided_by UUID REFERENCES users(id) ON DELETE SET NULL,
            decided_at TIMESTAMPTZ,
            cancelled_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_hr_requests_id_school_id UNIQUE (id, school_id),
            CONSTRAINT fk_hr_requests_employee_same_school FOREIGN KEY (employee_id, school_id)
                REFERENCES employees (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_hr_requests_type_allowed CHECK (request_type IN
                ('PROFILE_UPDATE', 'DOCUMENT_REQUEST', 'LETTER_REQUEST', 'HR_QUERY', 'OTHER')),
            CONSTRAINT ck_hr_requests_status_allowed CHECK (status IN
                ('PENDING', 'IN_REVIEW', 'APPROVED', 'REJECTED', 'RESOLVED', 'CANCELLED')),
            CONSTRAINT ck_hr_requests_source_allowed CHECK (source IN ('EMPLOYEE', 'ADMIN')),
            CONSTRAINT ck_hr_requests_decided_has_time CHECK (
                NOT (status IN ('APPROVED', 'REJECTED', 'RESOLVED')) OR decided_at IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_requests_school_status ON hr_requests (school_id, status)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_requests_school_employee ON hr_requests (school_id, employee_id)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_request_events (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            request_id UUID NOT NULL,
            actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            event_type VARCHAR(16) NOT NULL,
            from_status VARCHAR(16),
            to_status VARCHAR(16),
            message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_hr_request_events_request_same_school FOREIGN KEY (request_id, school_id)
                REFERENCES hr_requests (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_hr_request_events_event_allowed
                CHECK (event_type IN ('SUBMITTED', 'STATUS_CHANGED', 'COMMENT', 'CANCELLED'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_request_events_request ON hr_request_events (request_id, created_at)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS hr_request_events")
    op.execute("DROP TABLE IF EXISTS hr_requests")
