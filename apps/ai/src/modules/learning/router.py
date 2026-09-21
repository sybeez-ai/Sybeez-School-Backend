from fastapi import APIRouter

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/health")
async def health():
    return {"module": "learning", "status": "ok"}
