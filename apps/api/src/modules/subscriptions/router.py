from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/health")
async def subscriptions_health(_: Annotated[CurrentUser, Depends(require_permission("subscriptions.read"))]):
    return {"module": "subscriptions", "status": "ok"}
