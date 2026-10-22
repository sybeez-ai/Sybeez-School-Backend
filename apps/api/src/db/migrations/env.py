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
# ConfigParser treats % as interpolation. RDS passwords are percent-encoded.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))


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

# Created by migrations with raw SQL and intentionally not declared on the models: partial or
# expression unique indexes, pgvector HNSW indexes, legacy short-named indexes, and the
# ``parent_ai_reports`` table that ``ai_reports/repository.py`` uses through raw SQL.
# Autogenerate would otherwise propose dropping them.
MIGRATION_MANAGED = frozenset(
    {
        "parent_ai_reports",
        "ix_parent_ai_reports_created_at",
        "ix_parent_ai_reports_parent_user_id",
        "ix_parent_ai_reports_school_id",
        "ix_parent_ai_reports_student_id",
        "ix_admission_applications_submitted_at",
        "ix_admission_status_history_to_status",
        "ix_bookstore_items_category",
        "uq_enrollment_class_roll_active",
        "uq_enrollment_student_year_active",
        "ix_fee_adjustments_account",
        "ix_fee_audit_logs_school",
        "ix_fee_installments_structure",
        "ix_fee_invoice_items_invoice",
        "ix_fee_invoices_account",
        "uq_fee_invoice_account_installment",
        "ix_fee_payment_allocations_payment",
        "ix_fee_payments_gateway_order",
        "uq_fee_payment_idempotency",
        "ix_fee_receipts_payment",
        "ix_fee_structure_items_structure",
        "ix_knowledge_chunks_embedding_hnsw",
        "ix_student_ai_memory_embedding_hnsw",
        "ix_personal_habit_checkins_date",
        "ix_personal_notes_school_user",
        "ix_practice_attempts_created_at",
        "ix_saved_reports_type",
        "ix_student_fee_accounts_student",
        "uq_students_school_admission_no",
        "uq_students_school_student_code",
        "uq_teachers_school_employee_code",
        "uq_teachers_school_teacher_code",
        "uq_timetable_section_day_period",
    }
)


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (reflected and compare_to is None and name in MIGRATION_MANAGED)


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
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
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
