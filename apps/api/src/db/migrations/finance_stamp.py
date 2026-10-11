"""Move a production database off the pre-renumber finance revision ids.

RDS `sybeez-school-db` was migrated by the finance branch. Its
`alembic_version` row is `0030`, and that id meant `ai_shared_columns`, not
`0030_hr_departments_designations`. The same collision exists for `0028` and
`0029`. Leaving the stamp in place makes `upgrade head` skip HRM and payroll.

This module runs before Alembic applies anything. It restamps a recognised
finance-chain database to `0027` (the last revision both histories share) and
then returns. The following upgrade applies HRM `0028`–payroll `0040`, then
re-runs finance `0041`–`0043`, which are idempotent.

A stamp that matches neither chain, or both, raises. The database is left
unchanged and the migrate task exits non-zero.
"""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool

LEGACY_VERSIONS = ("0028", "0029", "0030")
STAMP_TARGET = "0027"


def assert_master_url(url: str, *, iam: bool) -> None:
    """Migrations use the master password in DATABASE_URL, never an IAM token."""
    if not iam:
        return
    parsed = urlparse(_as_postgres_url(url))
    if parsed.password:
        return
    raise RuntimeError(
        "Refusing to migrate: DB_IAM_AUTH is set but DATABASE_URL has no password. "
        "The migrate task must use the master user URL. IAM auth is only for sybeez_app "
        "on the API and worker."
    )


def classify(version: str | None, *, hr: bool, finance: bool) -> str:
    """Return skip, hrm, finance, or ambiguous."""
    if version not in LEGACY_VERSIONS:
        return "skip"
    if hr and finance:
        return "ambiguous"
    if hr:
        return "hrm"
    if finance:
        return "finance"
    return "ambiguous"


def align_database(url: str) -> str:
    """Commit a finance-chain restamp, or raise before any other DDL."""
    engine = create_engine(_sync_url(url), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            return _align(connection)
    finally:
        engine.dispose()


def _align(connection: Connection) -> str:
    version = _read_version(connection)
    if version is None or version not in LEGACY_VERSIONS:
        return "skip"
    hr, finance = _markers(connection, version)
    decision = classify(version, hr=hr, finance=finance)
    if decision == "ambiguous":
        raise RuntimeError(
            "Refusing to upgrade. alembic_version is "
            f"{version}, hr_marker={hr}, finance_marker={finance}. "
            "That is neither the HRM chain nor the legacy finance chain "
            f"({_marker_help(version)}). The database was not modified."
        )
    if decision != "finance":
        return decision
    result = connection.execute(
        text("UPDATE alembic_version SET version_num = :target WHERE version_num = :old"),
        {"target": STAMP_TARGET, "old": version},
    )
    if result.rowcount != 1:
        raise RuntimeError(
            f"Refusing to upgrade. Expected to restamp one row from {version} to {STAMP_TARGET}, "
            f"updated {result.rowcount}."
        )
    print(
        f"Detected legacy finance revision {version} "
        f"(finance objects present, HRM objects for that id absent). "
        f"Restamped alembic_version to {STAMP_TARGET}. "
        "upgrade will apply HRM 0028 through payroll 0040, then idempotent finance 0041-0043, "
        "then payroll 0044-0046, then platform access 0047-0049, then role aliases 0050.",
        file=sys.stderr,
        flush=True,
    )
    return "finance"


def _read_version(connection: Connection) -> str | None:
    if connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar() is None:
        return None
    rows = connection.execute(text("SELECT version_num FROM alembic_version")).fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"Refusing to upgrade. Expected one alembic_version row, found {len(rows)}."
        )
    return str(rows[0][0]).strip()


def _markers(connection: Connection, version: str) -> tuple[bool, bool]:
    if version == "0028":
        return _constraint(connection, "uq_teachers_id_school_id"), _finance_0028(connection)
    if version == "0029":
        return _table(connection, "employees"), _can_see(connection)
    if version == "0030":
        return _table(connection, "departments"), (_can_see(connection) or _finance_0028(connection))
    return False, False


def _marker_help(version: str) -> str:
    if version == "0028":
        return "HRM has constraint uq_teachers_id_school_id; finance has finance_staff_scopes or fee_categories.is_80c_eligible"
    if version == "0029":
        return "HRM has table employees; finance has function app.can_see"
    return "HRM has table departments; finance has app.can_see or finance_staff_scopes, and no departments"


def _table(connection: Connection, name: str) -> bool:
    return connection.execute(text("SELECT to_regclass(:name)"), {"name": f"public.{name}"}).scalar() is not None


def _constraint(connection: Connection, name: str) -> bool:
    return (
        connection.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
            {"name": name},
        ).first()
        is not None
    )


def _finance_0028(connection: Connection) -> bool:
    if _table(connection, "finance_staff_scopes"):
        return True
    row = connection.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'fee_categories'
              AND column_name = 'is_80c_eligible'
            """
        )
    ).first()
    return row is not None


def _can_see(connection: Connection) -> bool:
    row = connection.execute(
        text(
            """
            SELECT 1
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'app' AND p.proname = 'can_see'
            """
        )
    ).first()
    return row is not None


def _as_postgres_url(url: str) -> str:
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg2://", "postgresql+psycopg://"):
        if url.startswith(prefix):
            return "postgresql://" + url[len(prefix) :]
    return url


def _sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql+psycopg://"):
        return "postgresql+psycopg2://" + url[len("postgresql+psycopg://") :]
    return url
