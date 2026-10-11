from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.db.models import AuditLog
from src.modules.access.deps import require_permission
from src.shared.pagination import Page, PageQuery

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditOut(BaseModel):
    id: str
    action: str
    resource_type: str | None
    resource_id: str | None
    message: str | None
    actor_id: str | None
    details: dict | None

    model_config = {"from_attributes": True}


@router.get("", response_model=Page[AuditOut])
async def list_audit(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    page: PageQuery,
    action: str | None = None,
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    stmt = select(AuditLog).where(AuditLog.school_id == user.school_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(AuditLog.created_at.desc()).offset(page.offset).limit(page.page_size))
    ).scalars().all()
    items = [
        AuditOut(
            id=str(row.id),
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            message=row.message,
            actor_id=str(row.actor_id) if row.actor_id else None,
            details=row.details,
        )
        for row in rows
    ]
    return Page(items=items, total=int(total), page=page.page, page_size=page.page_size)
