"""HRM Phase 14: every HRM endpoint answers bad input with a clean 4xx, never a 500 or an internal detail.

Routes come from the OpenAPI schema, so new HRM endpoints are covered automatically.
"""

import re
from datetime import date
from uuid import uuid4

import pytest

from src.main import app

LEAK_MARKERS = re.compile(
    r"Traceback|sqlalchemy|asyncpg|psycopg|IntegrityError|DataError|duplicate key|violates|"
    r"File \"|\\\\Users\\\\|/usr/|site-packages|SELECT |INSERT |UPDATE |\^\(",
    re.IGNORECASE,
)
LONG = "x" * 5000
SKIP = {("PUT", "/api/v1/hr/files/{doc_id}/file")}  # multipart upload, covered by its own tests


def _hr_operations():
    spec = app.openapi()
    for path, ops in spec["paths"].items():
        if not path.startswith("/api/v1/hr"):
            continue
        for method, op in ops.items():
            if (method.upper(), path) not in SKIP:
                yield method.upper(), path, op


SPEC = app.openapi()
OPERATIONS = list(_hr_operations())


def _resolve(schema):
    while "$ref" in schema:
        schema = SPEC["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    if "anyOf" in schema:
        options = [s for s in schema["anyOf"] if s.get("type") != "null"]
        return _resolve(options[0]) if options else {}
    if "allOf" in schema and len(schema["allOf"]) == 1:
        return _resolve(schema["allOf"][0])
    return schema


def _sample(schema, *, long_strings=False, bad_enums=False):
    schema = _resolve(schema)
    kind = schema.get("type")
    if "enum" in schema:
        return "NOT_A_VALUE" if bad_enums else schema["enum"][0]
    if kind == "object" or "properties" in schema:
        return {
            name: _sample(prop, long_strings=long_strings, bad_enums=bad_enums)
            for name, prop in schema.get("properties", {}).items()
        }
    if kind == "array":
        return [_sample(schema.get("items", {}), long_strings=long_strings, bad_enums=bad_enums)]
    if kind == "string":
        fmt = schema.get("format")
        if fmt == "uuid":
            return str(uuid4())
        if fmt == "date":
            return date.today().isoformat()
        if fmt == "date-time":
            return f"{date.today().isoformat()}T09:00:00Z"
        if "pattern" in schema:
            return "NOT_A_VALUE" if bad_enums else None
        return LONG if long_strings else "Sample"
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    return None


def _body_schema(op):
    content = op.get("requestBody", {}).get("content", {})
    return content.get("application/json", {}).get("schema")


def _url(path, value):
    return re.sub(r"\{[^}]+\}", value, path)


def _assert_clean(r, label):
    assert r.status_code < 500, f"{label} -> {r.status_code}: {r.text[:300]}"
    assert not LEAK_MARKERS.search(r.text), f"{label} leaked internals: {r.text[:300]}"
    if r.status_code >= 400 and r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        assert set(body) == {"error"}, f"{label} non-standard error shape: {r.text[:200]}"
        assert body["error"]["code"] and body["error"]["message"]
        assert '"input"' not in r.text and LONG not in r.text, f"{label} echoed the request: {r.text[:200]}"
    assert r.headers.get("x-request-id"), label


async def _send(api, hr_world, method, url, **kwargs):
    """Calls as the HR admin, or as a teacher for employee-only endpoints."""
    r = await api.request(method, url, headers=hr_world.admin_a.headers, **kwargs)
    if r.status_code == 403 and "Requires one of" in r.text:
        r = await api.request(method, url, headers=hr_world.teacher_a.headers, **kwargs)
    return r


@pytest.mark.parametrize(("method", "path", "op"), [o for o in OPERATIONS if "{" in o[1]], ids=lambda v: v if isinstance(v, str) else "")
async def test_malformed_and_unknown_ids_are_rejected_cleanly(api, hr_world, method, path, op):
    body = _sample(_body_schema(op)) if _body_schema(op) else None
    for value, expected in (("not-a-uuid", {422}), (str(uuid4()), {404, 422})):
        r = await _send(api, hr_world, method, _url(path, value), json=body)
        _assert_clean(r, f"{method} {path} [{value}]")
        assert r.status_code in expected, f"{method} {path} [{value}] -> {r.status_code}: {r.text[:200]}"


@pytest.mark.parametrize(
    ("method", "path", "op"),
    [o for o in OPERATIONS if _body_schema(o[2]) and "{" not in o[1]],
    ids=lambda v: v if isinstance(v, str) else "",
)
async def test_bad_bodies_are_rejected_cleanly(api, hr_world, method, path, op):
    schema = _body_schema(op)
    variants = {
        "empty": {},
        "long strings": _sample(schema, long_strings=True),
        "bad enums": _sample(schema, bad_enums=True),
        "not an object": [1, 2, 3],
    }
    for label, body in variants.items():
        r = await _send(api, hr_world, method, path, json=body)
        _assert_clean(r, f"{method} {path} [{label}]")


@pytest.mark.parametrize(("method", "path", "op"), [o for o in OPERATIONS if o[0] == "GET" and "{" not in o[1]], ids=lambda v: v if isinstance(v, str) else "")
async def test_bad_query_parameters_are_rejected_cleanly(api, hr_world, method, path, op):
    params = {}
    for p in op.get("parameters", []):
        if p.get("in") != "query":
            continue
        schema = _resolve(p.get("schema", {}))
        kind, fmt = schema.get("type"), schema.get("format")
        params[p["name"]] = {"uuid": "nope", "date": "2026-13-45"}.get(fmt) or (
            "abc" if kind in {"integer", "number"} else "NOT_A_VALUE" if ("enum" in schema or "pattern" in schema) else LONG
        )
    if not params:
        pytest.skip("no query parameters")
    r = await _send(api, hr_world, "GET", path, params=params)
    _assert_clean(r, f"GET {path} {sorted(params)}")
    assert r.status_code in {400, 404, 422}, f"GET {path} accepted bad parameters: {r.status_code}"
