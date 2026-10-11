from fastapi import APIRouter
from src.modules.access.deps import public

router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.get("/health")
@public
async def assignments_health():
    return {"module": "assignments", "status": "ok"}
