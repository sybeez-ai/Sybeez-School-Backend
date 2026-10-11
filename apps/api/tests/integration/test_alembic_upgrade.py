"""Migrations apply through 0059 on an empty Postgres 16 database."""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2

API_ROOT = Path(__file__).resolve().parents[2]
DB_NAME = "sybeez_platform_migrate"
ADMIN_DSN = "postgresql://sybeez:sybeez@127.0.0.1:5432/postgres"
APP_DSN = f"postgresql://sybeez:sybeez@127.0.0.1:5432/{DB_NAME}"


def _admin():
    conn = psycopg2.connect(ADMIN_DSN)
    conn.autocommit = True
    return conn


def test_upgrade_head_on_empty_database():
    admin = _admin()
    cur = admin.cursor()
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (DB_NAME,),
    )
    cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
    cur.execute(f"CREATE DATABASE {DB_NAME} OWNER sybeez")
    admin.close()

    ext = psycopg2.connect(APP_DSN)
    ext.autocommit = True
    ext.cursor().execute("CREATE EXTENSION IF NOT EXISTS vector")
    ext.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"postgresql+asyncpg://sybeez:sybeez@127.0.0.1:5432/{DB_NAME}"
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=API_ROOT,
        env=env,
    )

    conn = psycopg2.connect(APP_DSN)
    cur = conn.cursor()
    cur.execute("SELECT version_num FROM alembic_version")
    assert cur.fetchone()[0] == "0059"
    cur.execute("SELECT count(*) FROM alert_message_templates WHERE school_id IS NULL")
    assert cur.fetchone()[0] == 33
    cur.execute(
        """
        SELECT relname, relforcerowsecurity
        FROM pg_class
        WHERE relname IN ('alert_message_templates', 'alert_channel_opt_outs', 'school_alert_settings')
        ORDER BY relname
        """
    )
    assert {name: forced for name, forced in cur.fetchall()} == {
        "alert_channel_opt_outs": True,
        "alert_message_templates": True,
        "school_alert_settings": True,
    }
    cur.execute("SELECT to_regclass('public.hr_probations')")
    assert cur.fetchone()[0] == "hr_probations"
    cur.execute("SELECT to_regclass('public.fee_route_transfers')")
    assert cur.fetchone()[0] == "fee_route_transfers"
    cur.execute("SELECT to_regclass('public.expense_heads')")
    assert cur.fetchone()[0] == "expense_heads"
    cur.execute("SELECT current_setting('server_version_num')")
    assert int(cur.fetchone()[0]) >= 160000

    cur.execute(
        """
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relname IN (
            'membership_permission_overrides', 'platform_staff', 'payroll_school_settings',
            'refresh_tokens', 'permissions', 'role_permissions'
        )
        ORDER BY relname
        """
    )
    flags = {name: (rls, force) for name, rls, force in cur.fetchall()}
    for name in ("membership_permission_overrides", "platform_staff", "payroll_school_settings"):
        assert flags[name] == (True, True), name
    for name in ("refresh_tokens", "permissions", "role_permissions"):
        assert flags[name] == (False, False)
    cur.execute(
        """
        SELECT pg_get_expr(pol.polqual, pol.polrelid)
        FROM pg_policy pol
        JOIN pg_class c ON c.oid = pol.polrelid
        WHERE c.relname = 'payroll_school_settings' AND pol.polname = 'payroll_school_settings_role'
        """
    )
    assert "payroll_is_staff" in cur.fetchone()[0]
    cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname = 'sybeez_app'")
    assert cur.fetchone()[0] is False

    cur.execute(
        """
        SELECT relrowsecurity AND relforcerowsecurity
        FROM pg_class
        WHERE relname = 'platform_staff'
        """
    )
    assert cur.fetchone()[0] is True
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'refresh_tokens' AND column_name IN ('family_expires_at', 'absolute_session')
        """
    )
    assert {row[0] for row in cur.fetchall()} == {"family_expires_at", "absolute_session"}
    cur.execute(
        """
        SELECT proname FROM pg_proc
        WHERE proname IN ('rls_role', 'rls_has', 'rls_tenant', 'rls_staff_portal')
        """
    )
    assert {row[0] for row in cur.fetchall()} == {"rls_role", "rls_has", "rls_tenant", "rls_staff_portal"}
    cur.execute(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'fee_settings' AND column_name = 'linked_account_id'
        """
    )
    assert cur.fetchone()[0] == "linked_account_id"
    cur.execute(
        """
        SELECT relforcerowsecurity FROM pg_class WHERE relname = 'fee_route_transfers'
        """
    )
    assert cur.fetchone()[0] is True
    conn.close()
