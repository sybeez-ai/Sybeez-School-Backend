from fastapi import APIRouter

router = APIRouter(prefix="/context", tags=["context"])


@router.get("/health")
async def health():
    return {"module": "context", "status": "ok"}
