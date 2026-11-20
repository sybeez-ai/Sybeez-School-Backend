"""Client-supplied X-Request-ID values are echoed into logs, headers and error bodies, so only safe tokens are kept."""

import re

import pytest
from fastapi.testclient import TestClient

from src.main import create_app

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _client() -> TestClient:
    app = create_app()

    @app.get("/__boom")
    async def boom():
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def test_generated_when_absent():
    r = _client().get("/api/v1/hr/leave/requests")
    assert r.status_code == 401
    assert UUID_RE.match(r.headers["x-request-id"])


@pytest.mark.parametrize("value", ["req-123", "a1b2.c3:d4_e5", "x" * 128])
def test_safe_client_value_is_kept(value):
    r = _client().get("/__boom", headers={"X-Request-ID": value})
    assert r.headers["x-request-id"] == value
    assert r.json()["error"]["request_id"] == value


@pytest.mark.parametrize("value", ["x" * 129, "<script>alert(1)</script>", "id with spaces", "a;b"])
def test_unsafe_client_value_is_replaced(value):
    r = _client().get("/__boom", headers={"X-Request-ID": value})
    rid = r.headers["x-request-id"]
    assert rid != value and UUID_RE.match(rid)
    assert r.json()["error"]["request_id"] == rid
    assert value not in r.text
