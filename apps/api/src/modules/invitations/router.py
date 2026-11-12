from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.get("/health")
async def invitations_health(_: Annotated[CurrentUser, Depends(require_permission("invitations.read"))]):
    return {"module": "invitations", "status": "ok"}
