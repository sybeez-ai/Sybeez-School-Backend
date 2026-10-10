"""Async SQLAlchemy engine.

Tenant scope is transaction-local. Payroll row-level security reads
app.school_id, app.user_id, app.role, and app.rls_bypass. A superuser
connection still bypasses forced RLS; production should use a NOBYPASSRLS
login. Unset school, user, and role fail closed in the payroll policies.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from src.core.config import get_settings

settings = get_settings()
engine = create_async_engine(settings.database_url, echo=settings.is_dev, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _apply_rls_local(session, _transaction, connection) -> None:
    if "rls_tenant" not in session.info:
        return
    tenant = session.info.get("rls_tenant") or {}
    connection.execute(text("SELECT set_config('app.school_id', :v, true)"), {"v": str(tenant.get("school_id") or "")})
    connection.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(tenant.get("user_id") or "")})
    connection.execute(text("SELECT set_config('app.role', :v, true)"), {"v": str(tenant.get("role") or "")})
    connection.execute(
        text("SELECT set_config('app.rls_bypass', :v, true)"),
        {"v": "on" if tenant.get("bypass") == "on" else "off"},
    )


event.listen(Session, "after_begin", _apply_rls_local)


def tenant_from_request(request: Request) -> dict[str, str]:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return {"school_id": "", "user_id": "", "role": "", "bypass": "off"}
    token = header.split(" ", 1)[1].strip()
    if not token:
        return {"school_id": "", "user_id": "", "role": "", "bypass": "off"}
    try:
        from src.core.security import decode_token

        payload = decode_token(token)
    except Exception:
        return {"school_id": "", "user_id": "", "role": "", "bypass": "off"}
    if payload.get("type") != "access":
        return {"school_id": "", "user_id": "", "role": "", "bypass": "off"}
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
