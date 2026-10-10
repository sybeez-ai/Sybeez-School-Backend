"""HRM Phase 2: employees (teaching + non-teaching workforce record).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0029"
down_revision: Union[str, None] = "0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS employees (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            staff_type VARCHAR(16) NOT NULL,
            teacher_id UUID,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            full_name VARCHAR(200),
            email VARCHAR(320),
            phone VARCHAR(32),
            employee_code VARCHAR(64),
            date_of_birth DATE,
            joining_date DATE,
            qualification VARCHAR(255),
            photo_url TEXT,
            status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
            employment_type VARCHAR(32),
            address TEXT,
            emergency_contact_name VARCHAR(200),
            emergency_contact_phone VARCHAR(32),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_employees_id_school_id UNIQUE (id, school_id),
            CONSTRAINT uq_employees_teacher_id UNIQUE (teacher_id),
            CONSTRAINT fk_employees_teacher_same_school FOREIGN KEY (teacher_id, school_id)
                REFERENCES teachers (id, school_id) ON DELETE CASCADE,
            CONSTRAINT ck_employees_staff_type_allowed CHECK (staff_type IN ('TEACHING', 'NON_TEACHING')),
            CONSTRAINT ck_employees_teacher_link_matches_type
                CHECK ((staff_type = 'TEACHING') = (teacher_id IS NOT NULL)),
            CONSTRAINT ck_employees_non_teaching_has_name CHECK (staff_type = 'TEACHING' OR full_name IS NOT NULL),
            CONSTRAINT ck_employees_status_allowed CHECK (status IN ('ACTIVE', 'INACTIVE', 'LEFT')),
            CONSTRAINT ck_employees_employment_type_allowed CHECK (
                employment_type IS NULL OR employment_type IN ('FULL_TIME', 'PART_TIME', 'CONTRACT', 'TEMPORARY')
            )
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_employees_school_id ON employees (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_employees_user_id ON employees (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_employees_status ON employees (status)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_employees_school_employee_code "
        "ON employees (school_id, lower(employee_code)) WHERE employee_code IS NOT NULL"
    )

    # Every existing teacher becomes a TEACHING employee; identity stays on teachers/users.
    op.execute(
        """
        INSERT INTO employees (id, school_id, staff_type, teacher_id, user_id, status)
        SELECT gen_random_uuid(), t.school_id, 'TEACHING', t.id, t.user_id, 'ACTIVE'
        FROM teachers t
        WHERE NOT EXISTS (SELECT 1 FROM employees e WHERE e.teacher_id = t.id)
        """
    )


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS employees")
