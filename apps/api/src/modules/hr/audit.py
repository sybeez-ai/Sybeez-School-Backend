"""Security-sensitive HR actions recorded in the shared ``audit_logs`` table."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog, User
from src.modules.audit.repository import AuditRepository
from src.shared.enums import UserRole

# Signed-in employees (teaching and non-teaching) use the same self-service endpoints.
SELF_SERVICE_ROLES = (UserRole.TEACHER, UserRole.STAFF)


def _plain(value):
    if isinstance(value, (UUID, date, datetime, time, Decimal)):
        return str(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    return value


async def hr_audit(
    db: AsyncSession,
    *,
    school_id: UUID,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: UUID | str,
    message: str | None = None,
    details: dict | None = None,
) -> None:
    await AuditRepository(db).write(
        action=action,
        actor_id=actor_id,
        school_id=school_id,
        resource_type=resource_type,
        resource_id=str(resource_id),
        message=message,
        details=_plain(details) if details else None,
    )


async def hr_audit_trail(
    db: AsyncSession, school_id: UUID, resource_type: str, resource_id: UUID, limit: int = 100
) -> list[dict]:
    rows = await db.execute(
        select(AuditLog, User.full_name)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(
            AuditLog.school_id == school_id,
            AuditLog.resource_type == resource_type,
            AuditLog.resource_id == str(resource_id),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "id": log.id,
            "action": log.action,
            "message": log.message,
            "details": log.details,
            "actor_name": actor,
            "created_at": log.created_at,
        }
        for log, actor in rows.all()
    ]
