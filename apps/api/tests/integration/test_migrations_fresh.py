"""A brand-new database must reach the Alembic head with no stamping or manual SQL,
and the resulting schema must match the ORM models (``alembic check``)."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.core.config import get_settings

API_DIR = Path(__file__).resolve().parents[2]


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": url, "PYTHONPATH": str(API_DIR)}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        check=False,
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.fixture
async def empty_database():
    base = make_url(get_settings().database_url)
    name = f"sybeez_migtest_{uuid4().hex[:10]}"
    admin = create_async_engine(base, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # noqa: BLE001
        await admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc}")
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_fresh_database_upgrades_to_head_and_matches_models(empty_database):
    upgrade = _alembic(empty_database, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr[-4000:]

    current = _alembic(empty_database, "current")
    heads = _alembic(empty_database, "heads")
    assert current.returncode == 0 and heads.returncode == 0
    head = heads.stdout.split()[0]
    assert head in current.stdout and "(head)" in current.stdout

    # 0042 adds ix_<table>_school indexes that are not on the models. That drift
    # predates this branch. The tables introduced here must not add to it.
    check = _alembic(empty_database, "check")
    drift = check.stdout + check.stderr
    for table in ("hr_leave_types", "hr_holidays", "hr_letter_templates", "employee_history"):
        assert table not in drift, drift[-4000:]

    downgrade = _alembic(empty_database, "downgrade", "-1")
    assert downgrade.returncode == 0, downgrade.stderr[-4000:]
    reupgrade = _alembic(empty_database, "upgrade", "head")
    assert reupgrade.returncode == 0, reupgrade.stderr[-4000:]
    again = _alembic(empty_database, "check")
    again_drift = again.stdout + again.stderr
    for table in ("hr_leave_types", "hr_holidays", "hr_letter_templates", "employee_history"):
        assert table not in again_drift, again_drift[-4000:]
