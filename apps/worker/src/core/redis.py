import redis

from src.core.config import get_settings


def redis_client_kwargs(url: str) -> dict:
    """Connection options for redis:// and rediss:// (TLS)."""
    kwargs: dict = {"decode_responses": True}
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = "required"
    return kwargs


def get_redis() -> redis.Redis:
    url = get_settings().redis_url
    return redis.Redis.from_url(url, **redis_client_kwargs(url))
