"""HR operations: reports, probation, appraisals, recruitment, training, roster, assets, exit.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-11

Tables are created from the ORM so names stay aligned with the models. Row-level
security follows 0052: a school policy plus a restrictive role policy. Teacher and
staff may read their own rows (and the shared catalogs). Writes stay with HR,
except a self or manager appraisal review.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0058"
down_revision: Union[str, None] = "0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Drop order is the reverse of creation.
_TABLES = (
    "hr_report_exports",
    "hr_probations",
    "hr_appraisal_cycles",
    "hr_appraisal_goals",
    "hr_appraisals",
    "hr_appraisal_reviews",
    "hr_job_openings",
    "hr_applications",
    "hr_interviews",
    "hr_offers",
    "hr_training_programs",
    "hr_training_records",
    "hr_shifts",
    "hr_roster_entries",
    "hr_assets",
    "hr_asset_issues",
    "hr_exit_cases",
    "hr_settlement_lines",
)

# Teacher and staff may read these school catalogs. They cannot write them.
_CATALOG = {"hr_appraisal_cycles", "hr_training_programs", "hr_shifts"}
# Own employee row, matched on subject_user_id so the policy does not re-enter employees RLS.
_OWN = {
    "hr_probations",
    "hr_training_records",
    "hr_roster_entries",
    "hr_asset_issues",
}
# Own row, or the reporting manager copied onto the row at write time.
_MANAGED = {"hr_appraisal_goals", "hr_appraisals"}

_HR = "app.can_see('staff', NULL::uuid, NULL::uuid, NULL::uuid) OR app.hr_access()"
_UID = "NULLIF(current_setting('app.user_id', true), '')::uuid"
_STAFF = "current_setting('app.role', true) IN ('TEACHER', 'STAFF')"


def _using(table: str) -> str:
    bypass = "current_setting('app.rls_bypass', true) = 'on'"
    if table in _CATALOG:
        return f"{bypass} OR {_HR} OR {_STAFF}"
    if table in _OWN:
        return f"{bypass} OR {_HR} OR ({_STAFF} AND subject_user_id = {_UID})"
    if table in _MANAGED:
        return f"{bypass} OR {_HR} OR subject_user_id = {_UID} OR manager_user_id = {_UID}"
    if table == "hr_appraisal_reviews":
        return f"""{bypass}
          OR {_HR}
          OR subject_user_id = {_UID}
          OR reviewer_user_id = {_UID}
          OR EXISTS (
            SELECT 1 FROM hr_appraisals a
            WHERE a.id = appraisal_id
              AND a.manager_user_id = {_UID}
              AND a.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
          )"""
    return f"{bypass} OR {_HR}"


def _check(table: str) -> str:
    if table != "hr_appraisal_reviews":
        return _HR
    return f"""{_HR}
      OR (
        kind = 'SELF'
        AND reviewer_user_id = {_UID}
        AND subject_user_id = {_UID}
      )
      OR (
        kind = 'MANAGER'
        AND reviewer_user_id = {_UID}
        AND EXISTS (
          SELECT 1 FROM hr_appraisals a
          WHERE a.id = appraisal_id
            AND a.manager_user_id = {_UID}
            AND a.school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
      )"""


def _secure(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {table}_school ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_school ON {table}
        USING (
          current_setting('app.rls_bypass', true) = 'on'
          OR school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        WITH CHECK (
          school_id = NULLIF(current_setting('app.school_id', true), '')::uuid
        )
        """
    )
    op.execute(f"DROP POLICY IF EXISTS {table}_role ON {table}")
    op.execute(
        f"""
        CREATE POLICY {table}_role ON {table} AS RESTRICTIVE
        USING ({_using(table)})
        WITH CHECK ({_check(table)})
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO sybeez_app")


def upgrade() -> None:
    from src.db.base import Base
    from src.db.models import hr_operations as ops

    tables = [Base.metadata.tables[name] for name in _TABLES]
    Base.metadata.create_all(op.get_bind(), tables=tables, checkfirst=True)
    for name in _TABLES:
        _secure(name)


def downgrade() -> None:
    for name in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS {name}")
