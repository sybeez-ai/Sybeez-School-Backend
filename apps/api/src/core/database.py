from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=settings.is_dev, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _reset_tenant_guc(session: AsyncSession) -> None:
    """Clear school scope before a pooled connection is reused.

    Row-level security is forced on finance tables, but PostgreSQL still lets
    the table owner and any superuser bypass it. The API login role must be
    sybeez_app (NOBYPASSRLS, created in migration 0028). A superuser
    DATABASE_URL does not enforce RLS.
    """
    try:
        await session.execute(text("SELECT set_config('app.school_id', '', false)"))
        await session.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))
        await session.commit()
    except Exception:
        try:
            await session.rollback()
        except Exception:
            pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await _reset_tenant_guc(session)
