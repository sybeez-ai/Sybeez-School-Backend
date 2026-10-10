"""HRM Phase 14: validation answers with the right status code and a readable, non-leaking message."""

import json
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import DataError, IntegrityError
from starlette.requests import Request

from src.core.middleware import database_error_response

RUNS = "/api/v1/hr/payroll/runs"
EMP = "/api/v1/hr/employees"
DEPTS = "/api/v1/hr/departments"
LEAVE = "/api/v1/hr/leave/requests"
LOGIN = "/api/v1/auth/login"


def _err(r):
    body = r.json()
    assert set(body) == {"error"}, r.text
    return body["error"]


async def _run(api, admin):
    r = await api.post(RUNS, json={"label": "Val run", "period_start": "2026-09-01", "period_end": "2026-09-30"}, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_payslip_amounts_must_fit_the_ledger(api, hr_world):
    admin = hr_world.admin_a.headers
    run_id = await _run(api, admin)
    url = f"{RUNS}/{run_id}/payslips"
    teacher = str(hr_world.teacher_a.teacher_id)
    for gross in ("10000000000.00", "100.123", "-1"):
        r = await api.post(url, json={"teacher_id": teacher, "gross_amount": gross}, headers=admin)
        assert r.status_code == 422, (gross, r.status_code, r.text)
        assert _err(r)["code"] == "validation_error"
        assert gross not in r.text
    ok = await api.post(url, json={"teacher_id": teacher, "gross_amount": "9999999999.99"}, headers=admin)
    assert ok.status_code == 201, ok.text
    r = await api.patch(f"/api/v1/hr/payroll/payslips/{ok.json()['id']}", json={"net_amount": "10000000000"}, headers=admin)
    assert r.status_code == 422, r.text


async def test_validation_errors_name_the_field_without_echoing_input(api, hr_world):
    r = await api.post(LOGIN, json={"email": "not-an-email", "password": "Sup3r-Secret!"})
    assert r.status_code == 422
    error = _err(r)
    assert error["message"] == "Email must be a valid email address"
    assert "Sup3r-Secret!" not in r.text and "not-an-email" not in r.text
    assert [f["field"] for f in error["fields"]] == ["email"]

    r = await api.get(f"{LEAVE}/not-a-uuid/decide", headers=hr_world.admin_a.headers)
    assert r.status_code in {404, 405, 422}
    r = await api.post(f"{LEAVE}/not-a-uuid/decide", json={"decision": "APPROVED"}, headers=hr_world.admin_a.headers)
    assert r.status_code == 422
    assert _err(r)["message"] == "Leave id must be a valid ID"

    r = await api.get("/api/v1/hr/payroll/payslips", params={"status": "SHIPPED"}, headers=hr_world.admin_a.headers)
    assert r.status_code == 422
    assert _err(r)["message"] == "Status has an invalid value"
    assert "^(" not in r.text

    r = await api.post(EMP, json={"staff_type": "NON_TEACHING"}, headers=hr_world.admin_a.headers)
    assert r.status_code == 422
    assert _err(r)["message"] == "Full name is required"


async def test_employee_password_has_an_upper_bound(api, hr_world):
    r = await api.post(
        EMP,
        json={
            "staff_type": "TEACHING",
            "full_name": "Long Password",
            "email": "long.password@example.com",
            "password": "A1!" + "x" * 200,
        },
        headers=hr_world.admin_a.headers,
    )
    assert r.status_code == 422, r.text
    assert _err(r)["message"] == "Password must be at most 128 characters"


async def test_routing_errors_use_the_standard_shape_and_keep_detail(api, hr_world):
    r = await api.get("/api/v1/hr/does-not-exist", headers=hr_world.admin_a.headers)
    assert r.status_code == 404
    assert r.json() == {"error": {"code": "not_found", "message": "Not Found"}, "detail": "Not Found"}
    r = await api.put("/api/v1/hr/dashboard", headers=hr_world.admin_a.headers)
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"
    assert r.headers.get("x-request-id")


def test_unchecked_database_errors_become_client_errors_without_sql():
    request = Request({"type": "http", "method": "POST", "path": "/api/v1/hr/x", "headers": [(b"x-request-id", b"req-7")]})
    cases = (
        (IntegrityError("INSERT INTO departments ...", {}, Exception('duplicate key value violates "uq"')), 409, "conflict"),
        (DataError("UPDATE payroll_payslips ...", {}, Exception("numeric field overflow")), 422, "invalid_value"),
    )
    for exc, status, code in cases:
        r = database_error_response(request, exc)
        body = json.loads(r.body)
        assert r.status_code == status
        assert body["error"]["code"] == code
        assert body["error"]["request_id"] == "req-7"
        assert not re.search(r"INSERT|UPDATE|duplicate|overflow|departments|payroll", r.body.decode())


async def test_conflicts_ranges_and_cross_school_references(api, hr_world):
    admin_a, admin_b = hr_world.admin_a.headers, hr_world.admin_b.headers
    dept = await api.post(DEPTS, json={"name": "Validation Office"}, headers=admin_a)
    assert dept.status_code == 201
    dup = await api.post(DEPTS, json={"name": "validation office"}, headers=admin_a)
    assert dup.status_code == 409, dup.text

    # Another school's department cannot be referenced.
    r = await api.post(EMP, json={"staff_type": "NON_TEACHING", "full_name": "Cross Ref", "department_id": dept.json()["id"]}, headers=admin_b)
    assert r.status_code in {404, 422}, r.text

    today = datetime.now(UTC).date()
    backwards = {"start_date": (today + timedelta(days=5)).isoformat(), "end_date": today.isoformat(), "reason": "Backwards"}
    r = await api.post(LEAVE, json=backwards, headers=hr_world.teacher_a.headers)
    assert r.status_code == 422, r.text

    leave = await api.post(
        LEAVE,
        json={"start_date": (today + timedelta(days=40)).isoformat(), "end_date": (today + timedelta(days=41)).isoformat(), "reason": "Once"},
        headers=hr_world.teacher_a.headers,
    )
    assert leave.status_code == 201, leave.text
    decide = f"{LEAVE}/{leave.json()['id']}/decide"
    assert (await api.post(decide, json={"decision": "APPROVED"}, headers=admin_a)).status_code == 200
    again = await api.post(decide, json={"decision": "REJECTED"}, headers=admin_a)
    assert again.status_code in {409, 422}, again.text
    assert (await api.post(decide, json={"decision": "APPROVED"}, headers=admin_b)).status_code == 404
