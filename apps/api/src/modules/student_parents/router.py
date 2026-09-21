from fastapi import APIRouter

router = APIRouter(prefix="/student-parents", tags=["student_parents"])


@router.get("/health")
async def health():
    return {"module": "student_parents", "status": "ok", "note": "Use POST /parents and /parents/by-student/{id}/link"}
