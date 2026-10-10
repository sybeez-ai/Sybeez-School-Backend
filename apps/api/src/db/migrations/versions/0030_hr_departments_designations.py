"""HRM Phase 3: departments, designations and employee assignment.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-10
"""

from typing import Sequence, Union

revision: str = "0030"
down_revision: Union[str, None] = "0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_EMPLOYEE_FKS = (
    ("fk_employees_department_same_school", "department_id", "departments"),
    ("fk_employees_designation_same_school", "designation_id", "designations"),
)


def _add_constraint_once(op, table: str, name: str, body: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = '{name}') THEN
                ALTER TABLE {table} ADD CONSTRAINT {name} {body};
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    from alembic import op

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            code VARCHAR(32),
            description TEXT,
            head_employee_id UUID,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_departments_id_school_id UNIQUE (id, school_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS designations (
            id UUID PRIMARY KEY,
            school_id UUID NOT NULL REFERENCES schools(id) ON DELETE CASCADE,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_designations_id_school_id UNIQUE (id, school_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_departments_school_id ON departments (school_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_designations_school_id ON designations (school_id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_departments_school_name ON departments (school_id, lower(name))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_designations_school_name ON designations (school_id, lower(name))"
    )

    op.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS department_id UUID")
    op.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS designation_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS ix_employees_department_id ON employees (department_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_employees_designation_id ON employees (designation_id)")
    for name, column, target in _EMPLOYEE_FKS:
        _add_constraint_once(
            op,
            "employees",
            name,
            f"FOREIGN KEY ({column}, school_id) REFERENCES {target} (id, school_id) ON DELETE SET NULL ({column})",
        )
    _add_constraint_once(
        op,
        "departments",
        "fk_departments_head_same_school",
        "FOREIGN KEY (head_employee_id, school_id) REFERENCES employees (id, school_id) "
        "ON DELETE SET NULL (head_employee_id)",
    )


def downgrade() -> None:
    from alembic import op

    op.execute("ALTER TABLE departments DROP CONSTRAINT IF EXISTS fk_departments_head_same_school")
    for name, column, _ in _EMPLOYEE_FKS:
        op.execute(f"ALTER TABLE employees DROP CONSTRAINT IF EXISTS {name}")
        op.execute(f"ALTER TABLE employees DROP COLUMN IF EXISTS {column}")
    op.execute("DROP TABLE IF EXISTS designations")
    op.execute("DROP TABLE IF EXISTS departments")
