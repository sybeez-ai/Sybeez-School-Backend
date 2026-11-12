"""School isolation.

Service methods filter by school_id. Postgres RLS is forced on tenant tables:
`school_id` must equal `current_setting('app.school_id')`, and a second policy
limits rows by `app.role` and `app.user_id`. Settings are transaction-local.

`app.rls_bypass=on` is only for the gateway webhook's payment lookup. Writes
still require a matching school_id. Leave the GUCs empty to fail closed.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError


def _tenant(db: AsyncSession) -> dict:
    current = db.info.get("rls_tenant")
    if not isinstance(current, dict):
        current = {}
        db.info["rls_tenant"] = current
    return current


async def apply_tenant(db: AsyncSession) -> None:
    """SET LOCAL the GUCs for the transaction already in progress."""
    tenant = _tenant(db)
    await db.execute(
        text("SELECT set_config('app.school_id', :v, true)"),
        {"v": str(tenant.get("school_id") or "")},
    )
    await db.execute(
        text("SELECT set_config('app.user_id', :v, true)"),
        {"v": str(tenant.get("user_id") or "")},
    )
    await db.execute(
        text("SELECT set_config('app.role', :v, true)"),
        {"v": str(tenant.get("role") or "")},
    )
    await db.execute(
        text("SELECT set_config('app.rls_bypass', :v, true)"),
        {"v": "on" if tenant.get("bypass") == "on" else "off"},
    )


async def bind_tenant(
    db: AsyncSession,
    school_id: UUID | None,
    *,
    user_id: UUID | None = None,
    role: str | None = None,
) -> None:
    tenant = _tenant(db)
    tenant["school_id"] = "" if school_id is None else str(school_id)
    tenant["bypass"] = "off"
    if user_id is not None:
        tenant["user_id"] = str(user_id)
    if role is not None:
        tenant["role"] = role
    await apply_tenant(db)


async def set_rls_bypass(db: AsyncSession, enabled: bool) -> None:
    tenant = _tenant(db)
    tenant["bypass"] = "on" if enabled else "off"
    await apply_tenant(db)


async def reset_tenant_guc(db: AsyncSession) -> None:
    db.info["rls_tenant"] = {}
    await apply_tenant(db)


def assert_same_school(row, school_id: UUID, what: str = "Record"):
    if row is None or getattr(row, "school_id", None) != school_id:
        raise NotFoundError(f"{what} not found")
    return row
