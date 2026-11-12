import httpx
from fastapi import APIRouter
from sqlalchemy import text

from src.core.config import get_settings
from src.core.database import engine
from src.core.redis import get_redis
from src.modules.access.deps import public

router = APIRouter(tags=["health"])


@router.get("/")
@public
async def root():
    """Load balancer default checks often use GET /."""
    return await health()


@router.get("/health")
@public
async def health():
    """Liveness. Redis being down is degraded, not a failed health check."""
    redis_ok = False
    try:
        redis = await get_redis()
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False
    return {
        "status": "ok" if redis_ok else "degraded",
        "service": "sybeez-api",
        "dependencies": {
            "redis": {"ok": redis_ok},
        },
    }


@router.get("/ready")
@public
async def ready():
    settings = get_settings()

    db_ok = False
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    redis_ok = False
    try:
        redis = await get_redis()
        redis_ok = bool(await redis.ping())
    except Exception:
        redis_ok = False

    ai_url = f"{settings.ai_service_url.rstrip('/')}/api/v1/ai/health"
    ai_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(ai_url)
            ai_ok = resp.status_code < 500
    except httpx.HTTPError:
        ai_ok = False

    all_ok = db_ok and redis_ok and ai_ok
    return {
        "status": "ready" if all_ok else "degraded",
        "service": "sybeez-api",
        "dependencies": {
            "database": {
                "ok": db_ok,
            },
            "redis": {
                "ok": redis_ok,
            },
            "ai_service": {
                "url": settings.ai_service_url,
                "ok": ai_ok,
            }
        },
    }
