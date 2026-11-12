class _RedisOk:
    async def ping(self):
        return True


class _RedisDown:
    async def ping(self):
        raise ConnectionError("redis down")


def test_health_ok_when_redis_responds(client, monkeypatch):
    async def get_redis():
        return _RedisOk()

    monkeypatch.setattr("src.modules.health.router.get_redis", get_redis)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"]["redis"]["ok"] is True


def test_health_degraded_when_redis_is_down(client, monkeypatch):
    async def get_redis():
        return _RedisDown()

    monkeypatch.setattr("src.modules.health.router.get_redis", get_redis)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["dependencies"]["redis"]["ok"] is False


def test_root_returns_the_same_liveness_payload(client, monkeypatch):
    async def get_redis():
        return _RedisDown()

    monkeypatch.setattr("src.modules.health.router.get_redis", get_redis)
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["service"] == "sybeez-api"


def test_health_degraded_when_redis_client_cannot_be_created(client, monkeypatch):
    async def get_redis():
        raise ConnectionError("dns")

    monkeypatch.setattr("src.modules.health.router.get_redis", get_redis)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


async def test_lifespan_listens_when_bootstrap_fails(monkeypatch):
    from src.main import app, lifespan

    async def fail(_session):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("src.main.bootstrap_superadmin", fail)
    async with lifespan(app):
        assert True
