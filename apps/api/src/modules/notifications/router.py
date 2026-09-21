from fastapi import APIRouter

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/health")
async def notifications_health():
    return {"module": "notifications", "status": "ok"}
