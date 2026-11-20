"""Library, activities, calendar, and the finance rows the screens call.

Revision ID: 0086
Revises: 0085

Additive only. Achievements already exist; this only adds their row security.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0086"
down_revision: Union[str, None] = "0085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CAMPUS = (
    "library_titles",
    "library_copies",
    "library_loans",
    "library_fines",
    "school_activities",
    "school_calendar_items",
)
FINANCE = (
    "caution_deposits",
    "book_refunds",
    "tds_entries",
    "gst_invoices",
)

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
FAMILY = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'TEACHER', 'STUDENT', 'PARENT')"
STAFF_WRITE = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'TEACHER')"
ADMIN_WRITE = f"{ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE')"
CAMPUS_READ = f"{BYPASS} OR ({SCHOOL} AND {FAMILY})"
CAMPUS_WRITE = f"{BYPASS} OR ({SCHOOL} AND {STAFF_WRITE})"
LIBRARY_WRITE = f"{BYPASS} OR ({SCHOOL} AND {ADMIN_WRITE})"
FINANCE_READ = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN "
    "('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT', 'CASHIER', 'AUDITOR', 'FINANCE'))"
)
FINANCE_WRITE = (
    f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN ('SCHOOL_ADMIN', 'PLATFORM_ADMIN', 'SERVICE', 'ACCOUNTANT'))"
)
ACH_READ = (
    f"{BYPASS} OR ({SCHOOL} AND ({STAFF_WRITE} OR app.can_see('student_data', student_id, NULL::uuid, NULL::uuid)))"
)
ACH_WRITE = f"{BYPASS} OR ({SCHOOL} AND {STAFF_WRITE})"


def _policy(table: str, read: str, write: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({write})")


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import books as _books
    from src.db.models import campus as _campus

    del _books, _campus
    names = list(CAMPUS) + list(FINANCE)
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[name] for name in names], checkfirst=True)
    library = {"library_titles", "library_copies", "library_loans", "library_fines"}
    for table in CAMPUS:
        _policy(table, CAMPUS_READ, LIBRARY_WRITE if table in library else CAMPUS_WRITE)
    for table in FINANCE:
        _policy(table, FINANCE_READ, FINANCE_WRITE)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON achievements TO sybeez_app")
    op.execute("ALTER TABLE achievements ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE achievements FORCE ROW LEVEL SECURITY")
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'achievements' AND policyname = 'achievements_select') THEN "
        f"CREATE POLICY achievements_select ON achievements FOR SELECT USING ({ACH_READ}); "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'achievements' AND policyname = 'achievements_insert') THEN "
        f"CREATE POLICY achievements_insert ON achievements FOR INSERT WITH CHECK ({ACH_WRITE}); "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'achievements' AND policyname = 'achievements_update') THEN "
        f"CREATE POLICY achievements_update ON achievements FOR UPDATE USING ({ACH_WRITE}) WITH CHECK ({ACH_WRITE}); "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'achievements' AND policyname = 'achievements_delete') THEN "
        f"CREATE POLICY achievements_delete ON achievements FOR DELETE USING ({ACH_WRITE}); "
        "END IF; END $$"
    )


def downgrade() -> None:
    for table in reversed(FINANCE + CAMPUS):
        op.execute(f"DROP TABLE IF EXISTS {table}")
