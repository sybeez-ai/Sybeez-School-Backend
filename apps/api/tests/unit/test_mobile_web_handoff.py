"""Mobile app: WebView session handoff (Bearer -> one-time code -> web cookies)."""

import pytest

from src.modules.auth import router as auth_router
from src.modules.auth.router import safe_next_path


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "/"),
        ("", "/"),
        ("/finance", "/finance"),
        ("/hrm?tab=leave", "/hrm?tab=leave"),
        ("//evil.com", "/"),
        ("/\\evil.com", "/"),
        ("https://evil.com", "/"),
        ("/x\r\nSet-Cookie: a=b", "/"),
    ],
)
def test_safe_next_path_blocks_open_redirects(raw, expected):
    assert safe_next_path(raw) == expected


def test_code_endpoint_requires_auth(client):
    assert client.post("/api/v1/auth/web-session/code").status_code == 401


def test_redeem_rejects_unknown_or_reused_code(client, monkeypatch):
    fake = FakeRedis()

    async def _get_redis():
        return fake

    monkeypatch.setattr(auth_router, "get_redis", _get_redis)
    res = client.get("/api/v1/auth/web-session", params={"code": "x" * 32}, follow_redirects=False)
    assert res.status_code == 401
    assert "set-cookie" not in res.headers
