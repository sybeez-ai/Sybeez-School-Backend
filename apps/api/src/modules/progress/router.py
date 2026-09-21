from fastapi import APIRouter

router = APIRouter(prefix="/progress", tags=["progress"])


@router.get("/health")
async def progress_health():
    return {"module": "progress", "status": "ok"}
