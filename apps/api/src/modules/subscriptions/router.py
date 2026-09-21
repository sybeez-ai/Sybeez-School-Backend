from fastapi import APIRouter

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


@router.get("/health")
async def subscriptions_health():
    return {"module": "subscriptions", "status": "ok"}
