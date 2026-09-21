from fastapi import APIRouter

router = APIRouter(prefix="/attendance", tags=["attendance"])


@router.get("/health")
async def attendance_health():
    return {"module": "attendance", "status": "ok"}
