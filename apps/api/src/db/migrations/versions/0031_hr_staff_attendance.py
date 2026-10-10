"""HRM Phase 4: staff attendance.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0031"
down_revision: Union[str, None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS staff_attendance (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            employee_id UUID NOT NULL,
            attendance_date DATE NOT NULL,
            status VARCHAR(16) NOT NULL,
            notes TEXT,
            marked_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_staff_attendance_employee_date UNIQUE (employee_id, attendance_date),
            CONSTRAINT fk_staff_attendance_employee_same_school FOREIGN KEY (employee_id, school_id)
                REFERENCES employees (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_staff_attendance_status_allowed
                CHECK (status IN ('PRESENT', 'ABSENT', 'HALF_DAY', 'LEAVE'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_staff_attendance_school_date ON staff_attendance (school_id, attendance_date)"
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS staff_attendance")
