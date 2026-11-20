"""Existence checks for migrations that must also run on a brand-new database.

``0001_initial`` builds the schema with ``Base.metadata.create_all`` from the *current*
models, so on an empty database objects that later revisions add already exist when those
revisions run. Revisions that use plain ``op.create_table`` / ``op.add_column`` /
``op.create_index`` skip the step when the object is already there. On databases that are
upgraded revision by revision the objects do not exist yet, so behaviour is unchanged.
"""

import sqlalchemy as sa
from alembic import op


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def table_exists(table: str) -> bool:
    return _inspector().has_table(table)


def column_exists(table: str, column: str) -> bool:
    return any(c["name"] == column for c in _inspector().get_columns(table))


def index_exists(name: str) -> bool:
    return op.get_bind().execute(sa.text("SELECT to_regclass(:n) IS NOT NULL"), {"n": name}).scalar_one()


def create_index_if_missing(name: str, table: str, columns: list[str], **kw) -> None:
    if not index_exists(name):
        op.create_index(name, table, columns, **kw)
