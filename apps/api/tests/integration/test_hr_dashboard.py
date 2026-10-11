"""HRM Phase 12: the HR dashboard is computed server-side and agrees with each module's own API."""

from datetime import UTC, datetime, timedelta

DASH = "/api/v1/hr/dashboard"
EMP = "/api/v1/hr/employees"
DEPTS = "/api/v1/hr/departments"
ATT = "/api/v1/hr/attendance"
LEAVE = "/api/v1/hr/leave/requests"
RUNS = "/api/v1/hr/payroll/runs"
PAYSLIPS = "/api/v1/hr/payroll/payslips"


def _today():
    # Test schools have no timezone, so the dashboard uses UTC.
    return datetime.now(UTC).date()


async def _dash(api, actor):
    r = await api.get(DASH, headers=actor.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_empty_school_reports_zeros_not_errors(api, hr_world):
    d = await _dash(api, hr_world.admin_b)
    assert d["as_of"] == _today().isoformat()
    assert d["school_timezone"] == "UTC"
    assert d["headcount"]["total"] == d["headcount"]["teaching"] == 1
    assert d["leave"] == {"pending": 0, "on_leave_today": 0, "approved_next_7_days": 0}
    assert d["salary_slips"] == {
        "pay_periods": 0,
        "latest_label": None,
        "latest_period_start": None,
        "latest_period_end": None,
        "latest_slips": 0,
        "latest_paid": 0,
    }
    assert d["workflows"]["tasks_total"] == 0


async def test_dashboard_matches_module_apis(api, hr_world):
    admin, teacher = hr_world.admin_a, hr_world.teacher_a
    today = _today()
    # Every day is a working day so the test does not depend on the weekday it runs on.
    every_day = await api.patch(
        "/api/v1/hr/settings", json={"working_weekdays": [0, 1, 2, 3, 4, 5, 6]}, headers=admin.headers
    )
    assert every_day.status_code == 200, every_day.text
    dept = (await api.post(DEPTS, json={"name": "Dash Office"}, headers=admin.headers)).json()
    cook = (
        await api.post(
            EMP,
            json={"staff_type": "NON_TEACHING", "full_name": "Dash Cook", "department_id": dept["id"]},
            headers=admin.headers,
        )
    ).json()
    clerk = (await api.post(EMP, json={"staff_type": "NON_TEACHING", "full_name": "Dash Clerk"}, headers=admin.headers)).json()
    assert (await api.post(f"{EMP}/{clerk['id']}/deactivate", headers=admin.headers)).status_code == 200
    mark = {"employee_id": cook["id"], "attendance_date": today.isoformat(), "status": "PRESENT"}
    assert (await api.post(ATT, json=mark, headers=admin.headers)).status_code == 201

    pending = await api.post(
        LEAVE,
        json={"start_date": (today + timedelta(days=20)).isoformat(), "end_date": (today + timedelta(days=21)).isoformat(), "reason": "Pending one"},
        headers=teacher.headers,
    )
    soon = await api.post(
        LEAVE,
        json={"start_date": (today + timedelta(days=3)).isoformat(), "end_date": (today + timedelta(days=3)).isoformat(), "reason": "Approved soon"},
        headers=hr_world.teacher_a2.headers,
    )
    assert pending.status_code == soon.status_code == 201
    decided = await api.post(f"{LEAVE}/{soon.json()['id']}/decide", json={"decision": "APPROVED"}, headers=admin.headers)
    assert decided.status_code == 200, decided.text

    run = (await api.post(RUNS, json={"label": "Dash Run", "period_start": "2026-07-01", "period_end": "2026-07-31"}, headers=admin.headers)).json()
    slip = (await api.post(f"{RUNS}/{run['id']}/payslips", json={"teacher_id": str(teacher.teacher_id), "gross_amount": "10"}, headers=admin.headers)).json()
    await api.post(f"{RUNS}/{run['id']}/payslips", json={"teacher_id": str(hr_world.teacher_a2.teacher_id), "gross_amount": "10"}, headers=admin.headers)
    await api.patch(f"{PAYSLIPS}/{slip['id']}", json={"status": "PAID"}, headers=admin.headers)

    d = await _dash(api, admin)

    employees = (await api.get(EMP, headers=admin.headers)).json()
    assert d["headcount"]["total"] == len(employees) == 4
    assert d["headcount"]["active"] == sum(e["is_active"] for e in employees) == 3
    assert d["headcount"]["inactive"] == 1
    assert (d["headcount"]["teaching"], d["headcount"]["non_teaching"]) == (2, 2)
    by_name = {x["name"]: x for x in d["departments"]}
    assert (by_name["Dash Office"]["active"], by_name["Dash Office"]["inactive"]) == (1, 0)
    assert by_name["No department"] == {"department_id": None, "name": "No department", "active": 2, "inactive": 1}

    sheet = (await api.get(f"{ATT}/daily", params={"date": today.isoformat()}, headers=admin.headers)).json()
    assert d["attendance_today"] == sheet["summary"]
    assert d["attendance_today"]["present"] == 1
    month = (
        await api.get(
            f"{ATT}/summary",
            params={"date_from": today.replace(day=1).isoformat(), "date_to": today.isoformat()},
            headers=admin.headers,
        )
    ).json()
    assert d["attendance_month"]["totals"] == month["totals"]

    assert d["leave"]["pending"] == len((await api.get(LEAVE, params={"status": "PENDING"}, headers=admin.headers)).json()) == 1
    assert d["leave"]["approved_next_7_days"] == 1
    assert d["leave"]["on_leave_today"] == 0

    checklist = (await api.get("/api/v1/hr/files/checklist", headers=admin.headers)).json()
    assert (d["records"]["checklist_complete"], d["records"]["checklist_incomplete"]) == (checklist["complete"], checklist["incomplete"])
    assert d["records"]["checklist_required"] == checklist["required"]

    s = d["salary_slips"]
    assert (s["pay_periods"], s["latest_label"], s["latest_slips"], s["latest_paid"]) == (1, "Dash Run", 2, 1)

    alerts = {a["key"]: a for a in d["alerts"]}
    assert alerts["leave"] == {"key": "leave", "severity": "warning", "count": 1, "message": "1 leave request waiting for a decision"}
    assert alerts["documents"]["count"] == checklist["incomplete"]
    assert alerts["attendance"]["count"] == sheet["summary"]["unmarked"]


async def test_dashboard_is_isolated_per_school(api, hr_world):
    a = hr_world.admin_a
    await api.post(EMP, json={"staff_type": "NON_TEACHING", "full_name": "Only In A"}, headers=a.headers)
    await api.post(RUNS, json={"label": "A Run", "period_start": "2026-07-01", "period_end": "2026-07-31"}, headers=a.headers)
    b = await _dash(api, hr_world.admin_b)
    assert b["headcount"]["total"] == 1
    assert b["salary_slips"]["pay_periods"] == 0
    assert all(x["name"] != "Only In A" for x in b["departments"])


async def test_as_of_follows_the_browser_date_within_a_day(api, hr_world):
    h = hr_world.admin_a.headers
    tomorrow = (_today() + timedelta(days=1)).isoformat()
    r = await api.get(DASH, params={"as_of": tomorrow}, headers=h)
    assert r.status_code == 200 and r.json()["as_of"] == tomorrow
    far = await api.get(DASH, params={"as_of": (_today() + timedelta(days=5)).isoformat()}, headers=h)
    assert far.status_code == 422
    assert far.json()["error"]["message"] == "as_of must be today's date"
    assert (await api.get(DASH, params={"as_of": "yesterday"}, headers=h)).status_code == 422


async def test_dashboard_roles(api, hr_world):
    for actor in (hr_world.teacher_a, hr_world.student_a):
        r = await api.get(DASH, headers=actor.headers)
        assert r.status_code == 403
    assert (await api.get(DASH)).status_code == 401
