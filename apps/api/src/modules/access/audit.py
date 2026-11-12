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


async def write_scoped_audit(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None,
    message: str,
    details: dict | None = None,
) -> None:
    """Audit as the signed-in user in their own school, for roles that cannot write audit_logs directly
    (parents, students). The database function takes school and user from the session, not arguments."""
    import json

    from sqlalchemy import text

    await db.execute(
        text("SELECT app.log_audit(:a, :t, :i, :m, CAST(:d AS jsonb))"),
        {"a": action, "t": resource_type, "i": resource_id, "m": message, "d": json.dumps(details or {}, default=str)},
    )
