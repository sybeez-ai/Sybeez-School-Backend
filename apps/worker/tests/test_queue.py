import json

from src.queue import QUEUE, handle_body, poll_redis, poll_sqs


def test_handle_body_dispatches_known_job(monkeypatch):
    seen = {}

    def handler(payload):
        seen.update(payload)

    monkeypatch.setitem(__import__("src.jobs", fromlist=["HANDLERS"]).HANDLERS, "fee_reminders", handler)
    handle_body(json.dumps({"type": "fee_reminders", "payload": {"school_id": "s1"}}))
    assert seen == {"school_id": "s1"}


def test_poll_redis_pushes_through_handler(monkeypatch):
    calls = []

    def handler(payload):
        calls.append(payload)

    monkeypatch.setitem(__import__("src.jobs", fromlist=["HANDLERS"]).HANDLERS, "payroll_generate", handler)

    class FakeRedis:
        def blpop(self, key, timeout=0):
            assert key == QUEUE
            return ("sybeez:jobs", json.dumps({"type": "payroll_generate", "payload": {"school_id": "s"}}))

    assert poll_redis(FakeRedis(), timeout=1) is True
    assert calls == [{"school_id": "s"}]


def test_poll_sqs_deletes_after_success(monkeypatch):
    calls = []

    def handler(payload):
        calls.append(payload["school_id"])

    monkeypatch.setitem(__import__("src.jobs", fromlist=["HANDLERS"]).HANDLERS, "fee_reminders", handler)

    class FakeSqs:
        def __init__(self):
            self.deleted = []

        def receive_message(self, **kwargs):
            assert kwargs["QueueUrl"] == "https://sqs.example/jobs"
            return {
                "Messages": [
                    {"Body": json.dumps({"type": "fee_reminders", "payload": {"school_id": "s"}}), "ReceiptHandle": "rh"}
                ]
            }

        def delete_message(self, **kwargs):
            self.deleted.append(kwargs["ReceiptHandle"])

    client = FakeSqs()
    assert poll_sqs(client, "https://sqs.example/jobs", wait_seconds=0) == 1
    assert calls == ["s"]
    assert client.deleted == ["rh"]


def test_rediss_worker_client_uses_tls():
    import redis
    from redis.connection import Connection, SSLConnection

    from src.core.redis import redis_client_kwargs

    secure = redis.Redis.from_url("rediss://cache.example:6379/0", **redis_client_kwargs("rediss://cache.example:6379/0"))
    plain = redis.Redis.from_url("redis://cache.example:6379/0", **redis_client_kwargs("redis://cache.example:6379/0"))
    try:
        assert secure.connection_pool.connection_class is SSLConnection
        assert plain.connection_pool.connection_class is Connection
    finally:
        secure.close()
        plain.close()


def test_empty_queue_read_timeout_is_idle_not_an_error():
    import redis

    from src.queue import poll_redis

    class Idle:
        def blpop(self, *a, **k):
            raise redis.exceptions.TimeoutError("Timeout reading from socket")

    assert poll_redis(Idle(), timeout=5) is False
