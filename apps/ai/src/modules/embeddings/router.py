from fastapi import APIRouter

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.get("/health")
async def health():
    return {"module": "embeddings", "status": "ok"}
