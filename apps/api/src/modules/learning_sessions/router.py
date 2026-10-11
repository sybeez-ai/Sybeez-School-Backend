from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/learning-sessions", tags=["learning_sessions"])


@router.get("/health")
async def learning_sessions_health(_: Annotated[CurrentUser, Depends(require_permission("learning_sessions.read"))]):
    return {"module": "learning_sessions", "status": "ok"}
