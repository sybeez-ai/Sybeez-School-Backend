"""A database stamped with the old finance 0030 must upgrade to head."""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text

API_ROOT = Path(__file__).resolve().parents[2]
LEGACY = API_ROOT / "src" / "db" / "migrations" / "legacy_finance"


async def _admin():
    try:
        return await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database="postgres")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable: {exc}")


def _alembic(name: str, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"postgresql+asyncpg://sybeez:sybeez@127.0.0.1:5432/{name}"
    env["APP_ENV"] = "development"
    env.pop("DB_IAM_AUTH", None)
    env.pop("DATABASE_IAM_AUTH", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=API_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _apply_legacy_finance(name: str) -> None:
    """Replay the finance-branch 0028–0030 SQL and stamp alembic_version at 0030."""
    url = f"postgresql+psycopg2://sybeez:sybeez@127.0.0.1:5432/{name}"
    engine = create_engine(url)
    try:
        for filename in (
            "0028_finance_production.py",
            "0029_platform_rls.py",
            "0030_ai_shared_columns.py",
        ):
            spec = importlib.util.spec_from_file_location(filename, LEGACY / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    module.upgrade()
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num = '0030'"))
    finally:
        engine.dispose()


async def _version(name: str) -> str:
    conn = await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database=name)
    try:
        return await conn.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await conn.close()


async def test_legacy_finance_0030_upgrades_to_head():
    name = f"sybeez_fin0030_{uuid4().hex[:10]}"
    admin = await _admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        base = _alembic(name, "upgrade", "0027")
        assert base.returncode == 0, base.stdout + "\n" + base.stderr
        _apply_legacy_finance(name)
        assert await _version(name) == "0030"
        departments = await _has_table(name, "departments")
        assert departments is False

        upgraded = _alembic(name, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + "\n" + upgraded.stderr
        assert "Detected legacy finance revision 0030" in upgraded.stderr
        assert "Restamped alembic_version to 0027" in upgraded.stderr
        assert await _version(name) == "0070"

        conn = await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database=name)
        try:
            assert await conn.fetchval("SELECT to_regclass('public.departments')") == "departments"
            assert await conn.fetchval("SELECT to_regclass('public.employees')") == "employees"
            assert await conn.fetchval("SELECT to_regclass('public.finance_staff_scopes')") == "finance_staff_scopes"
            assert await conn.fetchval("SELECT to_regclass('public.payroll_runs')") == "payroll_runs"
            assert await conn.fetchval("SELECT to_regclass('public.payroll_assistant_imports')") == "payroll_assistant_imports"
            assert await conn.fetchval("SELECT to_regclass('public.payroll_loan_events')") == "payroll_loan_events"
            mode = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'ai_conversations' AND column_name = 'mode'
                """
            )
            assert mode == 1
            can_see = await conn.fetchval(
                """
                SELECT 1 FROM pg_proc p
                JOIN pg_namespace n ON n.oid = p.pronamespace
                WHERE n.nspname = 'app' AND p.proname = 'can_see'
                """
            )
            assert can_see == 1
            forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_school_settings'"
            )
            assert forced is True
            fee_forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'fee_ledger_entries'"
            )
            assert fee_forced is True
            templates = await conn.fetchval(
                "SELECT count(*) FROM alert_message_templates WHERE school_id IS NULL"
            )
            assert templates == 50
            assert await conn.fetchval("SELECT to_regclass('public.school_alert_settings')") == "school_alert_settings"
        finally:
            await conn.close()
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()


async def test_hrm_0030_is_not_restamped():
    name = f"sybeez_hr0030_{uuid4().hex[:10]}"
    admin = await _admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        base = _alembic(name, "upgrade", "0030")
        assert base.returncode == 0, base.stdout + "\n" + base.stderr
        assert await _version(name) == "0030"
        assert "Restamped" not in base.stderr
        upgraded = _alembic(name, "upgrade", "head")
        assert upgraded.returncode == 0, upgraded.stdout + "\n" + upgraded.stderr
        assert "Restamped" not in upgraded.stderr
        assert await _version(name) == "0070"
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()


async def test_ambiguous_0030_refuses_and_leaves_the_stamp():
    name = f"sybeez_bad0030_{uuid4().hex[:10]}"
    admin = await _admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database=name)
        await conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        await conn.execute("INSERT INTO alembic_version (version_num) VALUES ('0030')")
        await conn.close()
        upgraded = _alembic(name, "upgrade", "head")
        assert upgraded.returncode != 0
        assert "Refusing to upgrade" in upgraded.stderr
        assert await _version(name) == "0030"
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()


async def _has_table(name: str, table: str) -> bool:
    conn = await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database=name)
    try:
        found = await conn.fetchval("SELECT to_regclass($1)", f"public.{table}")
        return found is not None
    finally:
        await conn.close()
