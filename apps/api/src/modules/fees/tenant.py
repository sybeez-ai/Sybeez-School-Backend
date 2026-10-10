"""School (and branch) isolation for finance.

Service methods always filter by school_id. Postgres RLS is the second guard:
session GUC `app.school_id` must match the row. `app.rls_bypass=on` may SELECT
(webhook lookup only); writes still require a matching school_id (WITH CHECK).

The GUC is session-scoped so it survives the mid-request commits used by
payments. `get_db` resets it before the connection returns to the pool.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError


async def bind_tenant(db: AsyncSession, school_id: UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.school_id', :sid, false)"),
        {"sid": str(school_id)},
    )
    await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))


async def set_rls_bypass(db: AsyncSession, enabled: bool) -> None:
    await db.execute(
        text("SELECT set_config('app.rls_bypass', :v, false)"),
        {"v": "on" if enabled else "off"},
    )


async def reset_tenant_guc(db: AsyncSession) -> None:
    await db.execute(text("SELECT set_config('app.school_id', '', false)"))
    await db.execute(text("SELECT set_config('app.rls_bypass', 'off', false)"))


def assert_same_school(row, school_id: UUID, what: str = "Record"):
    if row is None or getattr(row, "school_id", None) != school_id:
        raise NotFoundError(f"{what} not found")
    return row
