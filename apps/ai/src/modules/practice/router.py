from fastapi import APIRouter

router = APIRouter(prefix="/practice", tags=["practice"])


@router.get("/health")
async def health():
    return {"module": "practice", "status": "ok"}
