from fastapi import APIRouter

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/health")
async def analytics_health():
    return {"module": "analytics", "status": "ok"}
