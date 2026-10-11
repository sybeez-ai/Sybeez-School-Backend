from typing import Annotated

from fastapi import APIRouter, Depends

from src.api.dependencies import CurrentUser
from src.modules.access.deps import require_permission

router = APIRouter(prefix="/syllabus", tags=["syllabus"])


@router.get("/health")
async def syllabus_health(_: Annotated[CurrentUser, Depends(require_permission("syllabus.read"))]):
    return {"module": "syllabus", "status": "ok"}
