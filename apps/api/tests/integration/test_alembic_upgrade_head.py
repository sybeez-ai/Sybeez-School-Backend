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
            versions = await conn.fetch("SELECT version_num FROM alembic_version")
            assert [row["version_num"] for row in versions] == ["0077"]
            can_see = await conn.fetchval(
                "SELECT pg_get_functiondef('app.can_see(text,uuid,uuid,uuid)'::regprocedure)"
            )
            assert "r = 'TEACHER' AND row_user IS NOT NULL AND row_user = uid" in can_see
            templates = await conn.fetchval(
                "SELECT count(*) FROM alert_message_templates WHERE school_id IS NULL"
            )
            assert templates == 53
            forced_alerts = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'alert_message_templates'"
            )
            assert forced_alerts is True
            opt_out_forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'alert_channel_opt_outs'"
            )
            assert opt_out_forced is True
            probations = await conn.fetchval("SELECT to_regclass('public.hr_probations')")
            assert probations == "hr_probations"
            expenses = await conn.fetchval("SELECT to_regclass('public.expense_heads')")
            assert expenses == "expense_heads"
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
            fee_forced = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'fee_ledger_entries'"
            )
            assert fee_forced is True
            vector = await conn.fetchval("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            assert vector == 1
            payroll_policy = await conn.fetchval(
                """
                SELECT pg_get_expr(polqual, polrelid)
                FROM pg_policy
                WHERE polname = 'payroll_school_settings_role'
                """
            )
            assert payroll_policy and "payroll_is_staff" in payroll_policy
            tables = await conn.fetchval(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'payroll_runs'"
            )
            assert tables == 1
            imports = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_assistant_imports'"
            )
            assert imports is True
            loan_events = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_loan_events'"
            )
            assert loan_events is True
            lop_detail = await conn.fetchval(
                "SELECT 1 FROM information_schema.columns WHERE table_name = 'payroll_payslips' AND column_name = 'lop_detail'"
            )
            assert lop_detail == 1
            events = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_loan_events'"
            )
            assert events is True
            assistant = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_assistant_actions'"
            )
            assert assistant is True
            imports = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_assistant_imports'"
            )
            assert imports is True
            opening = await conn.fetchval(
                "SELECT relforcerowsecurity FROM pg_class WHERE relname = 'payroll_opening_ytd'"
            )
            assert opening is True
        finally:
            await conn.close()
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
            name,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()
