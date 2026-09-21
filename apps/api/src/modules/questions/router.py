from fastapi import APIRouter

router = APIRouter(prefix="/questions", tags=["questions"])


@router.get("/health")
async def questions_health():
    return {"module": "questions", "status": "ok"}
