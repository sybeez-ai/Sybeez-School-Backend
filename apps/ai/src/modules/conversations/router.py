from fastapi import APIRouter

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("/health")
async def health():
    return {"module": "conversations", "status": "ok"}
