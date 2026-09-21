from fastapi import APIRouter

router = APIRouter(prefix="/results", tags=["results"])


@router.get("/health")
async def results_health():
    return {"module": "results", "status": "ok"}
