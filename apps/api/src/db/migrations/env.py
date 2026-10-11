import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.core.config import Settings
from src.db.base import Base
from src.db import models  # noqa: F401 — register models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
# Migrations only need DATABASE_URL. The API's validate_runtime() also requires
# Cognito, S3, and JWT, which the migrate task does not carry.
settings = Settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def _migration_ssl() -> bool:
    if (settings.database_ssl_mode or "").lower() == "require":
        return True
    return (settings.app_env or "").lower() == "production"


def _sync_ssl_url(url: str) -> str:
    """psycopg2 reads sslmode from the URL. Leave local development URLs alone."""
    if not _migration_ssl() or "sslmode=" in url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}sslmode=require"


def _install_idempotent_ops() -> None:
    """0001 runs metadata.create_all() against the *current* models.

    A fresh database therefore already has columns and tables that later
    revisions also add. Those revisions must no-op when the object exists so
    `alembic upgrade head` works on an empty database and on a database that
    was migrated incrementally. Production upgrades still apply missing objects.
    """
    from alembic.operations import Operations
    from sqlalchemy import inspect

    orig_add_column = Operations.add_column

    def add_column(self, table_name, column, *, schema=None, if_not_exists=None, **kw):
        bind = self.get_bind()
        existing = {c["name"] for c in inspect(bind).get_columns(table_name, schema=schema)}
        if column.name in existing:
            return None
        return orig_add_column(
            self,
            table_name,
            column,
            schema=schema,
            if_not_exists=if_not_exists,
            **kw,
        )

    orig_create_index = Operations.create_index

    def create_index(self, index_name, table_name, columns, *, schema=None, unique=False, if_not_exists=None, **kw):
        return orig_create_index(
            self,
            index_name,
            table_name,
            columns,
            schema=schema,
            unique=unique,
            if_not_exists=True if if_not_exists is None else if_not_exists,
            **kw,
        )

    orig_create_table = Operations.create_table

    def create_table(self, table_name, *columns, if_not_exists=None, **kw):
        schema = kw.get("schema")
        bind = self.get_bind()
        if inspect(bind).has_table(table_name, schema=schema):
            from sqlalchemy import MetaData, Table

            return Table(table_name, MetaData(), schema=schema, autoload_with=bind)
        return orig_create_table(self, table_name, *columns, if_not_exists=if_not_exists, **kw)

    orig_unique = Operations.create_unique_constraint

    def create_unique_constraint(self, constraint_name, table_name, columns, *, schema=None, **kw):
        if _constraint_exists(self.get_bind(), constraint_name):
            return None
        return orig_unique(self, constraint_name, table_name, columns, schema=schema, **kw)

    orig_fk = Operations.create_foreign_key

    def create_foreign_key(self, constraint_name, source_table, referent_table, local_cols, remote_cols, **kw):
        if _constraint_exists(self.get_bind(), constraint_name):
            return None
        return orig_fk(
            self, constraint_name, source_table, referent_table, local_cols, remote_cols, **kw
        )

    Operations.add_column = add_column
    Operations.create_index = create_index
    Operations.create_table = create_table
    Operations.create_unique_constraint = create_unique_constraint
    Operations.create_foreign_key = create_foreign_key


def _constraint_exists(bind, name: str | None) -> bool:
    if not name:
        return False
    from sqlalchemy import text

    row = bind.execute(
        text("SELECT 1 FROM pg_constraint WHERE conname = :name"),
        {"name": name},
    ).first()
    return row is not None


_install_idempotent_ops()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from src.db.migrations.finance_stamp import align_database, assert_master_url

    # Master URL only. IAM tokens are for sybeez_app on the API and worker.
    assert_master_url(settings.database_url, iam=settings.database_iam_auth)
    # Commit a legacy-finance restamp before Alembic reads alembic_version.
    # An ambiguous stamp raises here, so the task exits before any other DDL.
    align_database(_sync_ssl_url(settings.database_url))
    asyncio.run(run_async_migrations())


async def run_async_migrations() -> None:
    connect_args = {"ssl": "require"} if _migration_ssl() else {}
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
