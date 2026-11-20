"""content_items: school content library tagged by board/grade/subject.

Revision ID: 0076
Revises: 0075
Create Date: 2026-10-18

RLS (per-operation): SELECT for school admins, teachers, students and service; no parent or finance/HR access.
INSERT/UPDATE/DELETE: school admins and teachers of that school. No personal data is stored (created_by is
nulled by user deletion rules: FK ON DELETE SET NULL; delete_my_account pseudonymises the user row in place).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0076"
down_revision: Union[str, None] = "0075"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "content_items"
SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
READ = f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN ('SCHOOL_ADMIN', 'TEACHER', 'STUDENT', 'PLATFORM_ADMIN', 'SERVICE'))"
WRITE = f"{BYPASS} OR ({SCHOOL} AND {ROLE} IN ('SCHOOL_ADMIN', 'TEACHER', 'PLATFORM_ADMIN', 'SERVICE'))"


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import content_library as models

    del models
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[TABLE]], checkfirst=True)
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO sybeez_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    for old in ("school", "role", "tenant", "access", "select", "insert", "update", "delete", "bypass", "read", "write"):
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_{old} ON {TABLE}")
    op.execute(f"CREATE POLICY {TABLE}_select ON {TABLE} FOR SELECT USING ({READ})")
    op.execute(f"CREATE POLICY {TABLE}_insert ON {TABLE} FOR INSERT WITH CHECK ({WRITE})")
    op.execute(f"CREATE POLICY {TABLE}_update ON {TABLE} FOR UPDATE USING ({WRITE}) WITH CHECK ({WRITE})")
    op.execute(f"CREATE POLICY {TABLE}_delete ON {TABLE} FOR DELETE USING ({WRITE})")


def downgrade() -> None:
    for op_name in ("select", "insert", "update", "delete"):
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_{op_name} ON {TABLE}")
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
