from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/health")
async def users_health():
    return {"module": "users", "status": "ok"}
