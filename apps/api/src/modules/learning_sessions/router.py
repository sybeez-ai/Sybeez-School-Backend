from fastapi import APIRouter

router = APIRouter(prefix="/learning-sessions", tags=["learning_sessions"])


@router.get("/health")
async def learning_sessions_health():
    return {"module": "learning_sessions", "status": "ok"}
