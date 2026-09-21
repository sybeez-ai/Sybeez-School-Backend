import httpx
from fastapi import APIRouter

from src.core.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok", "service": "sybeez-api"}


@router.get("/ready")
async def ready():
    settings = get_settings()
    ai_url = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/health"
    ai_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(ai_url)
            ai_ok = resp.status_code < 500
    except httpx.HTTPError:
        ai_ok = False

    return {
        "status": "ready" if ai_ok else "degraded",
        "service": "sybeez-api",
        "dependencies": {
            "ai_service": {
                "url": settings.ai_service_url,
                "ok": ai_ok,
            }
        },
    }
