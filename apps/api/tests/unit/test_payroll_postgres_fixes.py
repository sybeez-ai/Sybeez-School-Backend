"""Postgres failures found while running payroll on a real database."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from src.db.migrations.sqlutil import sql_statements
from src.modules.payroll.service import PayrollService


def test_sql_statements_splits_on_trailing_semicolons():
    script = """
    -- comment
    CREATE TABLE one (id UUID);

    CREATE TABLE two (id UUID);
    """
    assert sql_statements(script) == [
        "CREATE TABLE one (id UUID)",
        "CREATE TABLE two (id UUID)",
    ]


def test_payroll_table_script_is_one_statement_per_call():
    from pathlib import Path

    text = Path("src/db/migrations/versions/0040_payroll_production.py").read_text()
    start = text.index("_TABLES_SQL = r\"\"\"")
    body = text[start:].split('"""', 2)[1]
    statements = sql_statements(body)
    assert len(statements) > 20
    assert all(";" not in statement for statement in statements)
    assert sum(statement.startswith("CREATE TABLE") for statement in statements) >= 20


def test_initial_revision_does_not_create_current_models():
    from pathlib import Path

    text = Path("src/db/migrations/versions/0001_initial.py").read_text()
    upgrade = text.split("def upgrade", 1)[1].split("def downgrade", 1)[0]
    assert "create_all" not in upgrade
    assert "0001_initial_snapshot.sql" in text
    snapshot = Path("src/db/migrations/versions/0001_initial_snapshot.sql").read_text()
    statements = sql_statements(snapshot)
    assert any(statement.startswith("CREATE TABLE users") for statement in statements)
    assert "created_by_id" not in snapshot
    assert "platform_school_access" not in snapshot


class _Result:
    def scalars(self):
        return self

    def __iter__(self):
        return iter(())


class _Session:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return _Result()


async def test_leave_filter_without_teacher_is_sql_false():
    session = _Session()
    user = SimpleNamespace(id=uuid4(), school_id=uuid4(), role=SimpleNamespace(value="SCHOOL_ADMIN"))
    service = PayrollService(session, user)
    employee = SimpleNamespace(id=uuid4(), user_id=uuid4(), teacher_id=None)
    await service._marks(employee, date(2026, 10, 1), date(2026, 10, 31), [], [])
    leave = session.statements[1]
    compiled = str(leave.compile(dialect=postgresql.dialect())).lower()
    # false() must not be sent as a uuid subquery. OR false is removed by the compiler.
    assert compiled.count("select") == 1
    assert "teachers" not in compiled


async def test_leave_filter_with_teacher_uses_teacher_subquery():
    session = _Session()
    user = SimpleNamespace(id=uuid4(), school_id=uuid4(), role=SimpleNamespace(value="SCHOOL_ADMIN"))
    service = PayrollService(session, user)
    employee = SimpleNamespace(id=uuid4(), user_id=uuid4(), teacher_id=uuid4())
    await service._marks(employee, date(2026, 10, 1), date(2026, 10, 31), [], [])
    compiled = str(session.statements[1].compile(dialect=postgresql.dialect()))
    assert "teachers" in compiled
