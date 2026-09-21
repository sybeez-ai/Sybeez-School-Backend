from fastapi import APIRouter

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("/health")
async def announcements_health():
    return {"module": "announcements", "status": "ok"}
