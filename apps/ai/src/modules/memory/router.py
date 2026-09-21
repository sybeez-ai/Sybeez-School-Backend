from fastapi import APIRouter

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/health")
async def health():
    return {"module": "memory", "status": "ok"}
