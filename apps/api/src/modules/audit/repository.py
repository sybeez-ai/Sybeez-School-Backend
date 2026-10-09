"""Persistence for audit — always filter by visibility."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog, PlatformSchoolAccess, School, User


class AuditRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def write(
        self,
        *,
        action: str,
        actor_id: UUID | None = None,
        school_id: UUID | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        message: str | None = None,
        details: dict | None = None,
    ) -> AuditLog:
        log = AuditLog(
            actor_id=actor_id,
            school_id=school_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            message=message,
            details=details,
        )
        self.db.add(log)
        await self.db.flush()
        return log

    async def list_platform_activity(
        self,
        *,
        actor_id: UUID,
        is_admin: bool,
        school_id: UUID | None = None,
        limit: int = 200,
    ) -> list[tuple[AuditLog, str | None, str | None]]:
        """Returns [(audit_log, actor_email, school_name)] newest-first."""
        q = (
            select(AuditLog, User.email, School.name)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .outerjoin(School, School.id == AuditLog.school_id)
        )

        if school_id is not None:
            q = q.where(AuditLog.school_id == school_id)

        if not is_admin:
            visible_school_ids = (
                select(School.id)
                .outerjoin(PlatformSchoolAccess, PlatformSchoolAccess.school_id == School.id)
                .where(or_(School.created_by_id == actor_id, PlatformSchoolAccess.user_id == actor_id))
            )
            q = q.where(or_(AuditLog.actor_id == actor_id, AuditLog.school_id.in_(visible_school_ids)))

        q = q.order_by(desc(AuditLog.created_at)).limit(limit)
        result = await self.db.execute(q)
        return list(result.all())
