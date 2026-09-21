from fastapi import APIRouter

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/health")
async def health():
    return {"module": "evaluation", "status": "ok"}
