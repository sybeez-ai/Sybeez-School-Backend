import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.core.config import get_settings
from src.db.base import Base
from src.db import models  # noqa: F401 — register models

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

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


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
