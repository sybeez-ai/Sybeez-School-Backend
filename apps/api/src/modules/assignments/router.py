from fastapi import APIRouter

router = APIRouter(prefix="/assignments", tags=["assignments"])


@router.get("/health")
async def assignments_health():
    return {"module": "assignments", "status": "ok"}
