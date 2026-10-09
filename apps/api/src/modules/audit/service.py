"""Business logic for audit."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.audit.repository import AuditRepository
from src.modules.audit.schemas import AuditLogOut


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
            outs.append(
                AuditLogOut(
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
            )
        return outs
