from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/student-parents", tags=["student_parents"])


@router.get("/health")
async def health(_: Annotated[CurrentUser, Depends(require_permission("student_parents.read"))]):
    return {"module": "student_parents", "status": "ok"}
