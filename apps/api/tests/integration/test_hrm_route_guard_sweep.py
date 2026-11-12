"""Every HRM route, including ones added later, must reject anonymous callers and students.

Routes come from the live OpenAPI table, so a new endpoint without a role guard fails here.
"""

import re
from uuid import uuid4

import pytest

from src.main import app

PUBLIC = {
    ("GET", "/api/v1/hr/health"),
    # Authorised by the short-lived signed token in the URL (re-checked on redemption);
    # covered by test_hr_spec_compliance.
    ("GET", "/api/v1/hr/files/signed/{token}"),
}


def _hrm_routes() -> list[tuple[str, str]]:
    paths = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, ops in paths.items()
        if path.startswith("/api/v1/hr/") or path == "/api/v1/hr"
        for method in ops
        if (method.upper(), path) not in PUBLIC
    )


ROUTES = _hrm_routes()


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", lambda _: str(uuid4()), path)


def test_sweep_covers_the_hrm_surface():
    assert len(ROUTES) >= 80


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_anonymous_is_rejected(api, method, path):
    r = await api.request(method, _concrete(path), json={} if method != "GET" else None)
    assert r.status_code == 401, r.text


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
async def test_student_is_forbidden(api, hr_world, method, path):
    r = await api.request(
        method,
        _concrete(path),
        json={} if method != "GET" else None,
        headers=hr_world.student_a.headers,
    )
    assert r.status_code == 403, r.text
