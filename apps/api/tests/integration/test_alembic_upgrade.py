"""Migrations 0001 → 9001 → 9002 → 9003 apply on an empty Postgres 16 database."""

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
    assert cur.fetchone()[0] == "9003"
    cur.execute("SELECT current_setting('server_version_num')")
    assert int(cur.fetchone()[0]) >= 160000

    cur.execute(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN information_schema.columns col
          ON col.table_schema = n.nspname AND col.table_name = c.relname AND col.column_name = 'school_id'
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND NOT (c.relrowsecurity AND c.relforcerowsecurity)
        """
    )
    assert cur.fetchall() == []

    cur.execute(
        """
        SELECT relname, relrowsecurity, relforcerowsecurity
        FROM pg_class
        WHERE relname IN ('users', 'schools', 'syllabus_topics', 'refresh_tokens', 'permissions', 'role_permissions', 'alembic_version')
        ORDER BY relname
        """
    )
    flags = {name: (rls, force) for name, rls, force in cur.fetchall()}
    for name in ("users", "schools", "syllabus_topics"):
        assert flags[name] == (True, True)
    for name in ("refresh_tokens", "permissions", "role_permissions", "alembic_version"):
        assert flags[name] == (False, False)

    cur.execute("SELECT evtname FROM pg_event_trigger WHERE evtname = 'rls_protect_new_table'")
    assert cur.fetchone()[0] == "rls_protect_new_table"
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
        SELECT pg_get_expr(pol.polqual, pol.polrelid)
        FROM pg_policy pol
        JOIN pg_class c ON c.oid = pol.polrelid
        WHERE c.relname = 'students' AND pol.polname = 'actor_scope'
        """
    )
    assert "rls_has(" in cur.fetchone()[0]
    conn.close()
