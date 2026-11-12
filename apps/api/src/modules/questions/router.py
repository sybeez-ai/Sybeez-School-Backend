from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("/health")
async def questions_health(_: Annotated[CurrentUser, Depends(require_permission("questions.read"))]):
    return {"module": "questions", "status": "ok"}
