"""`alembic upgrade head` must build an empty Postgres 16 database."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

API_ROOT = Path(__file__).resolve().parents[2]


async def _admin():
    try:
        return await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database="postgres")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL not reachable: {exc}")


async def test_upgrade_head_on_empty_postgres():
    name = f"sybeez_upgrade_{uuid4().hex[:10]}"
    admin = await _admin()
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        env = os.environ.copy()
        env["DATABASE_URL"] = f"postgresql+asyncpg://sybeez:sybeez@127.0.0.1:5432/{name}"
        env["APP_ENV"] = "development"
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=API_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
        conn = await asyncpg.connect(user="sybeez", password="sybeez", host="127.0.0.1", database=name)
        try:
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
            assert version == "0040"
            created_by = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'schools' AND column_name = 'created_by_id'"
            )
            assert created_by == 1
            cognito = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'cognito_sub'"
            )
            assert cognito == 1
            forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_school_settings'"
            )
            assert forced is True
            tables = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'payroll_runs'"
            )
            assert tables == 1
        finally:
            await conn.close()
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()
