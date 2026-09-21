from fastapi import APIRouter

router = APIRouter(prefix="/syllabus", tags=["syllabus"])


@router.get("/health")
async def syllabus_health():
    return {"module": "syllabus", "status": "ok"}
