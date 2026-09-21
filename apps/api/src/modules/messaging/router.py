from fastapi import APIRouter

router = APIRouter(prefix="/messaging", tags=["messaging"])


@router.get("/health")
async def messaging_health():
    return {"module": "messaging", "status": "ok"}
