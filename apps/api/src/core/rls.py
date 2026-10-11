"""Bind the request token to Postgres session settings and the non-owner app role.

RLS does not apply to the table-owning superuser used by migrations and by
unauthenticated auth queries. Authenticated requests SET LOCAL ROLE sybeez_app
so tenant and actor policies are enforced for the rest of the transaction.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def apply_rls(
    session: AsyncSession,
    *,
    user_id: UUID,
    school_id: UUID | None,
    role: str,
    membership_id: UUID | None,
) -> None:
    await session.execute(text("SELECT set_config('app.user_id', :v, true)"), {"v": str(user_id)})
    await session.execute(
        text("SELECT set_config('app.school_id', :v, true)"),
        {"v": "" if school_id is None else str(school_id)},
    )
    await session.execute(text("SELECT set_config('app.role', :v, true)"), {"v": role})
    await session.execute(
        text("SELECT set_config('app.membership_id', :v, true)"),
        {"v": "" if membership_id is None else str(membership_id)},
    )
    await session.execute(text("SELECT set_config('app.lookup_email', '', true)"))
    connection = await session.connection()
    await connection.exec_driver_sql("SET LOCAL ROLE sybeez_app")


async def set_lookup_email(session: AsyncSession, email: str) -> None:
    await session.execute(
        text("SELECT set_config('app.lookup_email', :v, true)"),
        {"v": email.lower().strip()},
    )


async def clear_lookup_email(session: AsyncSession) -> None:
    await session.execute(text("SELECT set_config('app.lookup_email', '', true)"))
