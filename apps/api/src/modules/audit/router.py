from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.modules.audit.schemas import AuditLogOut
from src.modules.audit.service import AuditService
from src.shared.enums import PlatformRole, UserRole

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/health")
async def audit_health():
    return {"module": "audit", "status": "ok"}


@router.get("/logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    db: Annotated[AsyncSession, Depends(get_db)],
    actor: Annotated[CurrentUser, Depends(require_roles(UserRole.PLATFORM_ADMIN))],
    school_id: UUID | None = Query(default=None),
    limit: int = Query(200, ge=1, le=500),
):
    return await AuditService(db).list_platform_activity(
        actor_id=actor.id,
        is_admin=(actor.platform_role in {PlatformRole.SUPER_ADMIN.value, PlatformRole.ADMIN.value}),
        school_id=school_id,
        limit=limit,
    )
