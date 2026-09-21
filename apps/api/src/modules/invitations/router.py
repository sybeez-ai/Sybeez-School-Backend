from fastapi import APIRouter

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.get("/health")
async def invitations_health():
    return {"module": "invitations", "status": "ok"}
