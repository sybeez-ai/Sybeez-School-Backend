from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/health")
async def documents_health(_: Annotated[CurrentUser, Depends(require_permission("documents.read"))]):
    return {"module": "documents", "status": "ok"}
