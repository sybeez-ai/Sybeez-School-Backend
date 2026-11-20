"""feature controls: platform rules (global/per school) and school admin switches.

Revision ID: 0073
Revises: 0072
Create Date: 2026-10-18

RLS (per operation, forced):
- platform_feature_rules: SELECT global rows for any signed-in user, own-school rows for that school, everything for
  PLATFORM_ADMIN. INSERT/UPDATE/DELETE: PLATFORM_ADMIN only.
- school_feature_settings: SELECT for the school's own users (and PLATFORM_ADMIN). INSERT/UPDATE/DELETE: the school's
  SCHOOL_ADMIN in that school only. A school can therefore never read or change another school's switches.
No personal data: updated_by is an audit pointer to a user id (users are anonymised, never hard-deleted), so
delete_my_account needs no change for these tables.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0073"
down_revision: Union[str, None] = "0072"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHOOL = "school_id = NULLIF(current_setting('app.school_id', true), '')::uuid"
BYPASS = "current_setting('app.rls_bypass', true) = 'on'"
ROLE = "NULLIF(current_setting('app.role', true), '')"
SIGNED_IN = "NULLIF(current_setting('app.user_id', true), '') IS NOT NULL"


def _policies(table: str, read: str, write: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    for old in ("school", "role", "tenant", "access", "select", "insert", "update", "delete", "bypass", "read", "write"):
        op.execute(f"DROP POLICY IF EXISTS {table}_{old} ON {table}")
    op.execute(f"CREATE POLICY {table}_select ON {table} FOR SELECT USING ({read})")
    op.execute(f"CREATE POLICY {table}_insert ON {table} FOR INSERT WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_update ON {table} FOR UPDATE USING ({write}) WITH CHECK ({write})")
    op.execute(f"CREATE POLICY {table}_delete ON {table} FOR DELETE USING ({write})")


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import feature_controls as models

    del models
    tables = [Base.metadata.tables["platform_feature_rules"], Base.metadata.tables["school_feature_settings"]]
    Base.metadata.create_all(op.get_bind(), tables=tables, checkfirst=True)
    for t in ("platform_feature_rules", "school_feature_settings"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO sybeez_app")
        op.execute(f"GRANT SELECT ON {t} TO sybeez_definer")
    _policies(
        "platform_feature_rules",
        read=f"{BYPASS} OR {ROLE} = 'PLATFORM_ADMIN' OR (school_id IS NULL AND {SIGNED_IN}) OR {SCHOOL}",
        write=f"{BYPASS} OR {ROLE} = 'PLATFORM_ADMIN'",
    )
    _policies(
        "school_feature_settings",
        read=f"{BYPASS} OR {ROLE} = 'PLATFORM_ADMIN' OR {SCHOOL}",
        write=f"{BYPASS} OR ({SCHOOL} AND {ROLE} = 'SCHOOL_ADMIN')",
    )


def downgrade() -> None:
    for t in ("school_feature_settings", "platform_feature_rules"):
        for op_name in ("select", "insert", "update", "delete"):
            op.execute(f"DROP POLICY IF EXISTS {t}_{op_name} ON {t}")
        op.execute(f"DROP TABLE IF EXISTS {t}")
