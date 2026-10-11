"""Teacher self-service staff rows stay on migration 0054, after 0053.

0051 and 0052 are reserved for the HRM integration branch.
"""

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2] / "src" / "db" / "migrations" / "versions" / "0054_teacher_own_staff_row.py"
)


def test_teacher_own_staff_row_revises_0053():
    text = MIGRATION.read_text()
    assert 'revision: str = "0054"' in text
    assert 'down_revision: Union[str, None] = "0053"' in text
    assert "0051 and 0052 stay reserved" in text
    assert "IF r = 'TEACHER' AND row_user IS NOT NULL AND row_user = uid THEN" in text
    assert "ALTER FUNCTION app.can_see(text, uuid, uuid, uuid) OWNER TO sybeez_definer" in text
