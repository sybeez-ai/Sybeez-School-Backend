from fastapi import APIRouter

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/health")
async def health():
    return {"module": "retrieval", "status": "ok"}
