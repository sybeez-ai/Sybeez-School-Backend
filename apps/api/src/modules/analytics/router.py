from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/health")
async def analytics_health(_: Annotated[CurrentUser, Depends(require_permission("analytics.read"))]):
    return {"module": "analytics", "status": "ok"}
