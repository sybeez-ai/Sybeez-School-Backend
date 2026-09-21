from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/health")
async def documents_health():
    return {"module": "documents", "status": "ok"}
