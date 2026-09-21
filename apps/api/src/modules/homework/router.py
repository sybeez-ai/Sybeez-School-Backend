from fastapi import APIRouter

router = APIRouter(prefix="/homework", tags=["homework"])


@router.get("/health")
async def homework_health():
    return {"module": "homework", "status": "ok"}
