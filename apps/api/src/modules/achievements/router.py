from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("/health")
async def achievements_health(_: Annotated[CurrentUser, Depends(require_permission("achievements.read"))]):
    return {"module": "achievements", "status": "ok"}
