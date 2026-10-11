from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import get_settings
from src.core.exceptions import ForbiddenError

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=settings.is_dev, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:
            await session.rollback()
            if "row-level security" in str(exc).lower():
                raise ForbiddenError(
                    "You cannot access or change rows outside your school, class, or child scope"
                ) from exc
            raise
