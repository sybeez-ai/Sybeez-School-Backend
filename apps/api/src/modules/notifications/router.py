from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/health")
async def notifications_health(_: Annotated[CurrentUser, Depends(require_permission("notifications.read"))]):
    return {"module": "notifications", "status": "ok"}
