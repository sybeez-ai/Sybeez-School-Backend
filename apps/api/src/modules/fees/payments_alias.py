"""Alias the frontend already calls. Reading it does not create a payout."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.fees.permissions import FINANCE_VIEW
from src.modules.fees.route_service import RouteService
from src.modules.fees.tenant import bind_tenant

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/route")
async def payment_route_status(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*FINANCE_VIEW))],
):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    await bind_tenant(db, user.school_id)
    status = await RouteService(db).status(user.school_id)
    if hasattr(status, "model_dump"):
        body = status.model_dump()
    else:
        body = dict(status)
    body["mode"] = "test"
    body["live_payouts"] = False
    return body
