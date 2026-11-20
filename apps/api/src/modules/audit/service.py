"""Business logic for audit."""

from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog
from src.modules.audit.repository import AuditRepository
from src.modules.audit.schemas import AuditLogOut
from src.shared.listing import fetch_page
from src.shared.pagination import PageParams


def _out(log: AuditLog, actor_email: str | None = None, school_name: str | None = None) -> AuditLogOut:
    return AuditLogOut(
        id=log.id,
        created_at=log.created_at,
        school_id=log.school_id,
        school_name=school_name,
        actor_id=log.actor_id,
        actor_email=actor_email,
        action=log.action,
        message=log.message,
        details=log.details,
    )


class AuditService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuditRepository(db)

    async def list_platform_activity(
        self,
        *,
        actor_id: UUID,
        is_admin: bool,
        school_id: UUID | None = None,
        limit: int = 200,
    ) -> list[AuditLogOut]:
        rows = await self.repo.list_platform_activity(
            actor_id=actor_id, is_admin=is_admin, school_id=school_id, limit=limit
        )
        outs: list[AuditLogOut] = []
        for log, actor_email, school_name in rows:
            outs.append(_out(log, actor_email, school_name))
        return outs

    async def list_school(self, school_id: UUID, page: PageParams) -> tuple[list[AuditLogOut], int]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.school_id == school_id)
            .order_by(desc(AuditLog.created_at))
        )
        result, total = await fetch_page(self.db, stmt, page)
        return [_out(row) for row in result.scalars().all()], total

    async def list_actions(self, page: PageParams, action: str | None = None) -> tuple[list[AuditLogOut], int]:
        stmt = select(AuditLog).order_by(desc(AuditLog.created_at))
        if action:
            stmt = stmt.where(AuditLog.action == action)
        result, total = await fetch_page(self.db, stmt, page)
        return [_out(row) for row in result.scalars().all()], total
