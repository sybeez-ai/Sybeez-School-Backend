"""HRM Phase 7: onboarding and offboarding workflows with checklist tasks.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0033"
down_revision: Union[str, None] = "0032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_workflows (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            employee_id UUID NOT NULL,
            workflow_type VARCHAR(16) NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'OPEN',
            start_date DATE NOT NULL,
            expected_completion_date DATE,
            completed_at TIMESTAMPTZ,
            completed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            cancelled_at TIMESTAMPTZ,
            cancelled_by UUID REFERENCES users(id) ON DELETE SET NULL,
            cancel_reason TEXT,
            notes TEXT,
            owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            updated_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_hr_workflows_id_school_id UNIQUE (id, school_id),
            CONSTRAINT fk_hr_workflows_employee_same_school FOREIGN KEY (employee_id, school_id)
                REFERENCES employees (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_hr_workflows_type_allowed CHECK (workflow_type IN ('ONBOARDING', 'OFFBOARDING')),
            CONSTRAINT ck_hr_workflows_status_allowed
                CHECK (status IN ('OPEN', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')),
            CONSTRAINT ck_hr_workflows_expected_after_start
                CHECK (expected_completion_date IS NULL OR expected_completion_date >= start_date),
            CONSTRAINT ck_hr_workflows_completed_has_time
                CHECK (status <> 'COMPLETED' OR completed_at IS NOT NULL),
            CONSTRAINT ck_hr_workflows_cancelled_has_time
                CHECK (status <> 'CANCELLED' OR cancelled_at IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_hr_workflows_one_active_per_type "
        "ON hr_workflows (school_id, employee_id, workflow_type) WHERE status IN ('OPEN', 'IN_PROGRESS')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_workflows_school_status ON hr_workflows (school_id, status)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS hr_workflow_tasks (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            workflow_id UUID NOT NULL,
            title VARCHAR(200) NOT NULL,
            description TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_done BOOLEAN NOT NULL DEFAULT FALSE,
            completed_at TIMESTAMPTZ,
            completed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            due_date DATE,
            assignee_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            created_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT fk_hr_workflow_tasks_workflow_same_school FOREIGN KEY (workflow_id, school_id)
                REFERENCES hr_workflows (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_hr_workflow_tasks_done_has_time CHECK (NOT is_done OR completed_at IS NOT NULL)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_workflow_tasks_workflow_order "
        "ON hr_workflow_tasks (workflow_id, sort_order)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hr_workflow_tasks_assignee "
        "ON hr_workflow_tasks (school_id, assignee_user_id)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS hr_workflow_tasks")
    op.execute("DROP TABLE IF EXISTS hr_workflows")
