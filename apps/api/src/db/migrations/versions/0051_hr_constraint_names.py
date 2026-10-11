"""HRM: align primary/foreign key names with the model naming convention.

Migrations 0029-0035 created the HRM tables with raw SQL, so PostgreSQL named their
primary keys ``<table>_pkey`` and single-column foreign keys ``<table>_<column>_fkey``.
A fresh install builds the same tables from the models (``pk_<table>`` and
``fk_<table>_<column>_<referred_table>``). This rename makes both install paths carry the
same names, so later migrations can refer to them reliably. Metadata only; no data change.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-11

Renumbered from HRM 0036. Release already continues 0040–0050 after 0035, and
0002–0004 stay the revisions that were applied in production.
"""

from collections.abc import Sequence

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HR_TABLES = (
    "departments",
    "designations",
    "employees",
    "staff_attendance",
    "hr_letters",
    "employee_documents",
    "hr_workflows",
    "hr_workflow_tasks",
    "hr_requests",
    "hr_request_events",
    "hr_settings",
)

# For every PK/single-column FK on the listed tables: compute the PostgreSQL default name and
# the convention name, then rename FROM -> TO when FROM exists and TO is free.
_RENAME = """
DO $$
DECLARE
  r record;
  pg_name text;
  conv_name text;
BEGIN
  FOR r IN
    SELECT c.conname, c.contype, t.relname AS tbl, a.attname AS col, rt.relname AS ref
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace AND n.nspname = current_schema()
    LEFT JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
    LEFT JOIN pg_class rt ON rt.oid = c.confrelid
    WHERE t.relname = ANY (:tables)
      AND (c.contype = 'p' OR (c.contype = 'f' AND array_length(c.conkey, 1) = 1))
  LOOP
    IF r.contype = 'p' THEN
      pg_name := r.tbl || '_pkey';
      conv_name := 'pk_' || r.tbl;
    ELSE
      pg_name := r.tbl || '_' || r.col || '_fkey';
      conv_name := 'fk_' || r.tbl || '_' || r.col || '_' || r.ref;
    END IF;
    IF r.conname = :from_col AND NOT EXISTS (
      SELECT 1 FROM pg_constraint x
      WHERE x.conrelid = (quote_ident(r.tbl))::regclass AND x.conname = :to_col
    ) THEN
      EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I', r.tbl, :from_col, :to_col);
    END IF;
  END LOOP;
END $$;
"""


def _rename(from_col: str, to_col: str) -> None:
    from alembic import op

    tables = "ARRAY[" + ", ".join(f"'{t}'" for t in HR_TABLES) + "]::name[]"
    op.execute(
        _RENAME.replace(":tables", tables).replace(":from_col", from_col).replace(":to_col", to_col)
    )


def upgrade() -> None:
    _rename("pg_name", "conv_name")


def downgrade() -> None:
    _rename("conv_name", "pg_name")
