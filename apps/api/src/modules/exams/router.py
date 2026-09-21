from fastapi import APIRouter

router = APIRouter(prefix="/exams", tags=["exams"])


@router.get("/health")
async def exams_health():
    return {"module": "exams", "status": "ok"}
