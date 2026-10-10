"""Async SQLAlchemy engine.

Production connects as `sybeez_app` (NOLOGIN until you set a password, NOSUPERUSER,
NOBYPASSRLS). A superuser DATABASE_URL bypasses row-level security even when it
is FORCEd. Do not SET ROLE on pooled connections: TestClient closes the event
loop and a role-changed asyncpg connection then fails its next checkout.

Tenant scope is transaction-local (`SET LOCAL` / set_config third argument true).
`after_begin` applies it again after every commit inside a request. Unset
school, user, and role fail closed in the RLS policies.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from urllib.parse import quote

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from src.core.config import get_settings

settings = get_settings()


def _make_engine():
    if settings.database_iam_auth:
        import asyncpg

        async def _connect():
            return await asyncpg.connect(
                host=settings.database_host,
                port=settings.database_port,
                user=settings.database_user,
                password=settings.rds_auth_token(),
                database=settings.database_name,
                ssl="require",
            )

        url = (
            f"postgresql+asyncpg://{quote(settings.database_user)}@"
            f"{settings.database_host}:{settings.database_port}/{settings.database_name}"
        )
        return create_async_engine(
            url,
            async_creator=_connect,
            echo=settings.is_dev,
            pool_pre_ping=True,
        )
    return create_async_engine(
        settings.database_url,
        echo=settings.is_dev,
        pool_pre_ping=True,
        connect_args=settings.database_connect_args,
    )


engine = _make_engine()
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _apply_rls_local(session, _transaction, connection) -> None:
    if "rls_tenant" not in session.info:
        return
    tenant = session.info.get("rls_tenant") or {}
    connection.execute(
        text("SELECT set_config('app.school_id', :v, true)"),
        {"v": str(tenant.get("school_id") or "")},
    )
    connection.execute(
        text("SELECT set_config('app.user_id', :v, true)"),
        {"v": str(tenant.get("user_id") or "")},
    )
    connection.execute(
        text("SELECT set_config('app.role', :v, true)"),
        {"v": str(tenant.get("role") or "")},
    )
    connection.execute(
        text("SELECT set_config('app.rls_bypass', :v, true)"),
        {"v": "on" if tenant.get("bypass") == "on" else "off"},
    )


event.listen(Session, "after_begin", _apply_rls_local)


def tenant_from_request(request: Request) -> dict[str, str]:
    """Read school, user, and role from the access token. Empty when absent."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return {}
    token = header.split(" ", 1)[1].strip()
    if not token:
        return {}
    try:
        from src.core.security import decode_token

        payload = decode_token(token)
    except Exception:
        return {}
    if payload.get("type") != "access":
        return {}
    return {
        "school_id": str(payload.get("school_id") or ""),
        "user_id": str(payload.get("sub") or ""),
        "role": str(payload.get("role") or ""),
        "bypass": "off",
    }


async def get_db(request: Request) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        session.info["rls_tenant"] = tenant_from_request(request)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
