from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import AuditLog


async def write_audit(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    actor_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    message: str,
    details: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        school_id=school_id,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        message=message,
        details=details,
    )
    db.add(row)
    await db.flush()
    return row
