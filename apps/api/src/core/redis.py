from redis.asyncio import Redis

from src.core.config import get_settings

_redis: Redis | None = None


def redis_client_kwargs(url: str) -> dict:
    """Connection options for redis:// and rediss:// (TLS)."""
    kwargs: dict = {"decode_responses": True}
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = "required"
    return kwargs


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        url = get_settings().redis_url
        _redis = Redis.from_url(url, **redis_client_kwargs(url))
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
