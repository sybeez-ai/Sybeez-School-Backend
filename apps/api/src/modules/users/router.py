from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/health")
async def users_health(_: Annotated[CurrentUser, Depends(require_permission("users.read"))]):
    return {"module": "users", "status": "ok"}
