"""Async SQLAlchemy engine.

Production connects as `sybeez_app` (NOLOGIN until you set a password, NOSUPERUSER,
NOBYPASSRLS). A superuser DATABASE_URL bypasses row-level security even when it
is FORCEd. Do not SET ROLE on pooled connections: TestClient closes the event
loop and a role-changed asyncpg connection then fails its next checkout.

Tenant scope is transaction-local (`SET LOCAL` / set_config third argument true).
Payroll and finance policies read app.school_id, app.user_id, app.role, and
app.rls_bypass. `after_begin` applies them again after every commit inside a
request. Unset school, user, and role fail closed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Request
import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from src.core.config import get_settings
from src.core.exceptions import ForbiddenError

settings = get_settings()


logger = logging.getLogger(__name__)

def _make_engine():
    if settings.database_iam_auth:
        from src.core.rds_iam import create_iam_async_engine

        return create_iam_async_engine(
            database_url=settings.database_url,
            database_host=settings.database_host,
            database_port=settings.database_port,
            database_user=settings.database_user,
            database_name=settings.database_name,
            aws_region=settings.aws_region,
            echo=settings.is_dev,
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
        text("SELECT set_config('app.membership_id', :v, true)"),
        {"v": str(tenant.get("membership_id") or "")},
    )
    connection.execute(
        text("SELECT set_config('app.portals', :v, true)"),
        {"v": str(tenant.get("portals") or "")},
    )
    connection.execute(
        text("SELECT set_config('app.rls_bypass', :v, true)"),
        {"v": "on" if tenant.get("bypass") == "on" else "off"},
    )


event.listen(Session, "after_begin", _apply_rls_local)


def tenant_from_request(request: Request) -> dict[str, str]:
    """Read school, user, and role from the access token. Empty when absent."""
    header = request.headers.get("authorization") or ""
    token = ""
    if header.lower().startswith("bearer "):
        token = header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(settings.access_cookie_name) or ""
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
        except Exception as exc:
            await session.rollback()
            if "row-level security" in str(exc).lower():
                # Name the policy/table (never row values) so 403s are diagnosable.
                detail = str(getattr(exc, "orig", exc)).splitlines()[0][:200]
                logger.warning("RLS violation on %s %s: %s", request.method, request.url.path, detail)
                raise ForbiddenError(
                    "You cannot access or change rows outside your school, class, or child scope"
                ) from exc
            raise
