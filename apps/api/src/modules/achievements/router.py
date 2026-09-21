from fastapi import APIRouter

router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("/health")
async def achievements_health():
    return {"module": "achievements", "status": "ok"}
