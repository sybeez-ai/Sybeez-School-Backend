from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/health")
async def reports_health(_: Annotated[CurrentUser, Depends(require_permission("reports.read"))]):
    return {"module": "reports", "status": "ok"}
