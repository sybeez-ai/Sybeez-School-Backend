"""Worker database engine. IAM when DB_IAM_AUTH is set, otherwise DATABASE_URL."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from src.core.config import get_settings
from src.core.rds_iam import create_iam_async_engine

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    settings = get_settings()
    if settings.db_iam_auth:
        _engine = create_iam_async_engine(
            database_url=settings.database_url,
            database_host=settings.database_host,
            database_port=settings.database_port,
            database_user=settings.database_user,
            database_name=settings.database_name,
            aws_region=settings.aws_region,
        )
        return _engine
    if not (settings.database_url or "").strip():
        raise RuntimeError("Worker DATABASE_URL is not set")
    connect_args = {}
    if (settings.database_ssl_mode or "").lower() == "require":
        connect_args["ssl"] = "require"
    _engine = create_async_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
    return _engine
