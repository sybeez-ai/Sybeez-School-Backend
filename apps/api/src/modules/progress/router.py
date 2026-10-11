from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/health")
async def progress_health(_: Annotated[CurrentUser, Depends(require_permission("progress.read"))]):
    return {"module": "progress", "status": "ok"}
