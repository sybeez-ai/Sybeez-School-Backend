"""Regression: unhandled 500s lacked CORS headers, so browsers showed "Cannot reach API" instead of the error."""

from fastapi.testclient import TestClient

from src.main import create_app

ORIGIN = "http://localhost:3000"


def _client() -> TestClient:
    app = create_app()

    @app.get("/__boom")
    async def boom():
        raise RuntimeError("boom")

    return TestClient(app, raise_server_exceptions=False)


def test_unhandled_error_is_json_500_with_cors_headers():
    r = _client().get("/__boom", headers={"Origin": ORIGIN, "X-Request-ID": "req-123"})

    assert r.status_code == 500
    assert r.headers.get("access-control-allow-origin") == ORIGIN
    assert r.json() == {
        "error": {"code": "internal_error", "message": "Internal server error", "request_id": "req-123"}
    }
    assert r.headers.get("x-request-id") == "req-123"


def test_handled_errors_keep_cors_headers():
    r = _client().get("/api/v1/hr/leave/requests", headers={"Origin": ORIGIN})

    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == ORIGIN
