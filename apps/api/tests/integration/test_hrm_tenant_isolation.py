"""HRM Phase 1: role and school isolation matrix for every backend-backed HRM endpoint.

School A owns one leave request, one payroll run and one payslip (all for teacher_a). Each case
states the status a given actor must receive; data-returning cases also assert nothing leaks.
"""

from dataclasses import dataclass

import pytest

LEAVE = "/api/v1/hr/leave/requests"
RUNS = "/api/v1/hr/payroll/runs"
PAYSLIPS = "/api/v1/hr/payroll/payslips"
TEACHERS = "/api/v1/teachers"


@dataclass
class SchoolAData:
    leave_id: str
    run_id: str
    payslip_id: str
    teacher_id: str


@pytest.fixture
async def school_a(api, hr_world) -> SchoolAData:
    leave = await api.post(
        LEAVE,
        json={"start_date": "2026-12-01", "end_date": "2026-12-02", "reason": "Isolation fixture"},
        headers=hr_world.teacher_a.headers,
    )
    run = await api.post(
        RUNS,
        json={"label": "IT Dec 2026", "period_start": "2026-12-01", "period_end": "2026-12-31"},
        headers=hr_world.admin_a.headers,
    )
    slip = await api.post(
        f"{RUNS}/{run.json()['id']}/payslips",
        json={"teacher_id": str(hr_world.teacher_a.teacher_id), "gross_amount": "100.00"},
        headers=hr_world.admin_a.headers,
    )
    assert (leave.status_code, run.status_code, slip.status_code) == (201, 201, 201)
    return SchoolAData(
        leave_id=leave.json()["id"],
        run_id=run.json()["id"],
        payslip_id=slip.json()["id"],
        teacher_id=str(hr_world.teacher_a.teacher_id),
    )


def _requests(d: SchoolAData):
    return {
        "list leave": ("GET", LEAVE, None),
        "my leave": ("GET", f"{LEAVE}/me", None),
        "decide leave": ("POST", f"{LEAVE}/{d.leave_id}/decide", {"decision": "APPROVED"}),
        "cancel leave": ("POST", f"{LEAVE}/{d.leave_id}/cancel", None),
        "list runs": ("GET", RUNS, None),
        "get run": ("GET", f"{RUNS}/{d.run_id}", None),
        "add payslip": ("POST", f"{RUNS}/{d.run_id}/payslips", {"teacher_id": d.teacher_id, "gross_amount": "1"}),
        "update payslip": ("PATCH", f"{PAYSLIPS}/{d.payslip_id}", {"notes": "x"}),
        "payslip pdf": ("GET", f"{PAYSLIPS}/{d.payslip_id}/pdf", None),
        "my payslips": ("GET", f"{PAYSLIPS}/me", None),
        "list employees": ("GET", TEACHERS, None),
        "get employee": ("GET", f"{TEACHERS}/{d.teacher_id}", None),
        "update employee": ("PATCH", f"{TEACHERS}/{d.teacher_id}", {"title": "Mx"}),
        "employee status": ("PATCH", f"{TEACHERS}/{d.teacher_id}/status", {"is_active": True}),
    }


# actor -> {request name: expected status}. Requests omitted for an actor are not applicable.
EXPECTED = {
    "admin_b": {
        "list leave": 200,
        "decide leave": 404,
        "list runs": 200,
        "get run": 404,
        "add payslip": 404,
        "update payslip": 404,
        "payslip pdf": 404,
        "list employees": 200,
        "get employee": 404,
        "update employee": 404,
        "employee status": 404,
        "my leave": 403,
        "cancel leave": 403,
        "my payslips": 403,
    },
    "teacher_b": {
        "my leave": 200,
        "cancel leave": 404,
        "my payslips": 200,
        "payslip pdf": 404,
        "get employee": 404,
        "list leave": 403,
        "decide leave": 403,
        "list runs": 403,
        "get run": 403,
        "add payslip": 403,
        "update payslip": 403,
        "list employees": 403,
        "update employee": 403,
        "employee status": 403,
    },
    "teacher_a2": {
        "my leave": 200,
        "cancel leave": 403,
        "my payslips": 200,
        "payslip pdf": 403,
        "get employee": 403,
        "list leave": 403,
        "decide leave": 403,
        "list runs": 403,
        "get run": 403,
        "add payslip": 403,
        "update payslip": 403,
        "list employees": 403,
        "update employee": 403,
        "employee status": 403,
    },
    "student_a": {name: 403 for name in _requests(SchoolAData("x", "x", "x", "x"))},
    None: {name: 401 for name in _requests(SchoolAData("x", "x", "x", "x"))},
}

CASES = [(actor, name) for actor, table in EXPECTED.items() for name in table]


@pytest.mark.parametrize(("actor", "name"), CASES, ids=[f"{a or 'anonymous'}:{n}" for a, n in CASES])
async def test_hrm_endpoint_isolation(api, hr_world, school_a, actor, name):
    method, path, body = _requests(school_a)[name]
    headers = getattr(hr_world, actor).headers if actor else {}

    r = await api.request(method, path, json=body, headers=headers)

    assert r.status_code == EXPECTED[actor][name], r.text
    if r.status_code == 200 and method == "GET" and not path.endswith("/pdf"):
        leaked = {school_a.leave_id, school_a.run_id, school_a.payslip_id, school_a.teacher_id}
        assert not leaked & {str(row.get("id")) for row in r.json()}


async def test_same_school_admin_and_owner_have_access(api, hr_world, school_a):
    admin, owner = hr_world.admin_a.headers, hr_world.teacher_a.headers

    assert school_a.leave_id in {x["id"] for x in (await api.get(LEAVE, headers=admin)).json()}
    assert school_a.run_id in {x["id"] for x in (await api.get(RUNS, headers=admin)).json()}
    detail = (await api.get(f"{RUNS}/{school_a.run_id}", headers=admin)).json()
    assert [s["id"] for s in detail["payslips"]] == [school_a.payslip_id]
    assert school_a.teacher_id in {x["id"] for x in (await api.get(TEACHERS, headers=admin)).json()}
    assert (await api.get(f"{PAYSLIPS}/{school_a.payslip_id}/pdf", headers=admin)).status_code == 200

    assert [x["id"] for x in (await api.get(f"{LEAVE}/me", headers=owner)).json()] == [school_a.leave_id]
    assert [x["id"] for x in (await api.get(f"{PAYSLIPS}/me", headers=owner)).json()] == [school_a.payslip_id]
    assert (await api.get(f"{PAYSLIPS}/{school_a.payslip_id}/pdf", headers=owner)).status_code == 200
    assert (await api.get(f"{TEACHERS}/{school_a.teacher_id}", headers=owner)).status_code == 200
