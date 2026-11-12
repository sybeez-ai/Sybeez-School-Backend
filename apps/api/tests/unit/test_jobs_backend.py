import asyncio
import json

import pytest
from redis.asyncio import Redis
from redis.asyncio.connection import Connection, SSLConnection

from src.core.config import get_settings
from src.core.jobs import QUEUE, enqueue_job
from src.core.redis import redis_client_kwargs


def test_rediss_url_uses_tls():
    secure = Redis.from_url("rediss://cache.example:6379/0", **redis_client_kwargs("rediss://cache.example:6379/0"))
    plain = Redis.from_url("redis://cache.example:6379/0", **redis_client_kwargs("redis://cache.example:6379/0"))
    try:
        assert secure.connection_pool.connection_class is SSLConnection
        assert plain.connection_pool.connection_class is Connection
        assert redis_client_kwargs("rediss://cache.example:6379/0")["ssl_cert_reqs"] == "required"
    finally:
        asyncio.run(secure.aclose())
        asyncio.run(plain.aclose())


@pytest.mark.asyncio
async def test_enqueue_defaults_to_redis(monkeypatch):
    pushed: list[tuple] = []

    class FakeRedis:
        async def rpush(self, key, value):
            pushed.append((key, value))

    async def get_redis():
        return FakeRedis()

    settings = get_settings()
    monkeypatch.setattr(settings, "jobs_backend", "redis")
    monkeypatch.setattr("src.core.jobs.get_redis", get_redis)
    await enqueue_job("payroll_generate", {"school_id": "school-1"})
    assert pushed[0][0] == QUEUE
    body = json.loads(pushed[0][1])
    assert body == {"type": "payroll_generate", "payload": {"school_id": "school-1"}}


@pytest.mark.asyncio
async def test_enqueue_sqs_uses_queue_url(monkeypatch):
    sent: dict = {}

    def send(settings, body: str) -> None:
        sent["url"] = settings.sqs_queue_url
        sent["body"] = body

    settings = get_settings()
    monkeypatch.setattr(settings, "jobs_backend", "sqs")
    monkeypatch.setattr(settings, "sqs_queue_url", "https://sqs.ap-south-1.amazonaws.com/123/sybeez-jobs")
    monkeypatch.setattr("src.core.jobs.send_sqs_message", send)
    await enqueue_job("fee_reminders", {"school_id": "school-1"})
    assert sent["url"].endswith("/sybeez-jobs")
    assert json.loads(sent["body"])["type"] == "fee_reminders"


def test_sqs_requires_queue_url(monkeypatch):
    from src.core.jobs import send_sqs_message

    settings = get_settings()
    monkeypatch.setattr(settings, "sqs_queue_url", "")
    with pytest.raises(RuntimeError, match="SQS_QUEUE_URL"):
        send_sqs_message(settings, "{}")
