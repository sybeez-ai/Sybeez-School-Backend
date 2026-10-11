"""The payroll migration must force RLS and keep payslip access columns reversible."""

from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[2] / "src/db/migrations/versions/0040_payroll_production.py"


def test_migration_forces_rls_and_tracks_payslip_access():
    text = MIGRATION.read_text()
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "payroll_owns_payslip" in text
    assert "payroll_is_staff" in text
    assert "view_count" in text
    assert "download_count" in text
    assert "down_revision" in text and "0035" in text
    assert "DROP COLUMN IF EXISTS view_count" in text
    assert "sql_statements(_TABLES_SQL)" in text
    assert "op.execute(_TABLES_SQL)" not in text
