"""HRM Phase 11: read-only salary slip access on top of the Payroll API.

Employees only ever see PAID (final) payslips; admins see every payslip with the employee and
period it belongs to. Values are returned as Payroll stored them.
"""

import pytest

RUNS = "/api/v1/hr/payroll/runs"
PAYSLIPS = "/api/v1/hr/payroll/payslips"


async def _run(api, admin, label, start, end):
    r = await api.post(RUNS, json={"label": label, "period_start": start, "period_end": end}, headers=admin)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _slip(api, admin, run_id, teacher_id, gross="1000.00", deductions="100.00"):
    r = await api.post(
        f"{RUNS}/{run_id}/payslips",
        json={"teacher_id": str(teacher_id), "gross_amount": gross, "deductions_amount": deductions},
        headers=admin,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _set_status(api, admin, slip_id, status):
    r = await api.patch(f"{PAYSLIPS}/{slip_id}", json={"status": status}, headers=admin)
    assert r.status_code == 200, r.text


@pytest.fixture
async def slips(api, hr_world):
    admin, t = hr_world.admin_a.headers, hr_world.teacher_a.teacher_id
    sep = await _run(api, admin, "Sep 2026", "2026-09-01", "2026-09-30")
    oct_ = await _run(api, admin, "Oct 2026", "2026-10-01", "2026-10-31")
    dec25 = await _run(api, admin, "Dec 2025", "2025-12-01", "2025-12-31")
    paid_sep = await _slip(api, admin, sep, t)
    draft_oct = await _slip(api, admin, oct_, t)
    paid_dec = await _slip(api, admin, dec25, t, gross="900.00", deductions="0")
    other = await _slip(api, admin, sep, hr_world.teacher_a2.teacher_id)
    await _set_status(api, admin, paid_sep, "PAID")
    await _set_status(api, admin, paid_dec, "PAID")
    return {
        "runs": {"sep": sep, "oct": oct_, "dec25": dec25},
        "paid_sep": paid_sep,
        "draft_oct": draft_oct,
        "paid_dec": paid_dec,
        "other": other,
    }


async def test_employee_sees_only_own_paid_payslips(api, hr_world, slips):
    r = await api.get(f"{PAYSLIPS}/me", headers=hr_world.teacher_a.headers)
    assert r.status_code == 200
    body = r.json()
    assert [s["id"] for s in body] == [slips["paid_sep"], slips["paid_dec"]]
    first = body[0]
    assert first["status"] == "PAID" and first["paid_on"]
    assert first["run_label"] == "Sep 2026"
    assert (first["period_start"], first["period_end"]) == ("2026-09-01", "2026-09-30")
    assert (first["gross_amount"], first["deductions_amount"], first["net_amount"]) == ("1000.00", "100.00", "900.00")
    assert first["employee_code"]


async def test_employee_year_filter(api, hr_world, slips):
    h = hr_world.teacher_a.headers
    assert [s["id"] for s in (await api.get(f"{PAYSLIPS}/me?year=2025", headers=h)).json()] == [slips["paid_dec"]]
    assert (await api.get(f"{PAYSLIPS}/me?year=2030", headers=h)).json() == []
    assert (await api.get(f"{PAYSLIPS}/me?year=abc", headers=h)).status_code == 422


async def test_unpublished_payslip_pdf_is_hidden_from_employee(api, hr_world, slips):
    owner, admin = hr_world.teacher_a.headers, hr_world.admin_a.headers
    assert (await api.get(f"{PAYSLIPS}/{slips['draft_oct']}/pdf", headers=owner)).status_code == 404
    assert (await api.get(f"{PAYSLIPS}/{slips['draft_oct']}/pdf", headers=admin)).status_code == 200

    await _set_status(api, admin, slips["paid_sep"], "CANCELLED")
    assert (await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf", headers=owner)).status_code == 404
    mine = (await api.get(f"{PAYSLIPS}/me", headers=owner)).json()
    assert slips["paid_sep"] not in {s["id"] for s in mine}


async def test_employee_pdf_of_own_paid_slip(api, hr_world, slips):
    r = await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf", headers=hr_world.teacher_a.headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="payslip-IT-')
    assert disposition.endswith('-2026-09.pdf"')
    assert slips["paid_sep"] not in disposition
    assert b"Employee: IT TEACHER" in r.content
    assert b"01 Sep 2026 to 30 Sep 2026" in r.content


async def test_browser_can_read_download_filename(api, hr_world, slips):
    r = await api.get(
        f"{PAYSLIPS}/{slips['paid_sep']}/pdf",
        headers={**hr_world.teacher_a.headers, "Origin": "http://localhost:3000"},
    )
    exposed = {h.strip().lower() for h in r.headers.get("access-control-expose-headers", "").split(",")}
    assert {"content-disposition", "x-request-id"} <= exposed


async def test_other_employee_and_student_blocked(api, hr_world, slips):
    other = hr_world.teacher_a2.headers
    assert (await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf", headers=other)).status_code == 403
    assert (await api.get(f"{PAYSLIPS}/me", headers=other)).json() == []
    student = hr_world.student_a.headers
    assert (await api.get(f"{PAYSLIPS}/me", headers=student)).status_code == 403
    assert (await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf", headers=student)).status_code == 403
    assert (await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf")).status_code == 401


async def test_admin_history_has_names_periods_and_filters(api, hr_world, slips):
    admin = hr_world.admin_a.headers
    rows = (await api.get(PAYSLIPS, headers=admin)).json()
    assert {r["id"] for r in rows} == {slips["paid_sep"], slips["draft_oct"], slips["paid_dec"], slips["other"]}
    assert [r["period_start"] for r in rows] == sorted((r["period_start"] for r in rows), reverse=True)
    assert all(r["employee_name"] and r["employee_code"] and r["run_label"] for r in rows)

    async def ids(query):
        r = await api.get(f"{PAYSLIPS}?{query}", headers=admin)
        assert r.status_code == 200, r.text
        return {x["id"] for x in r.json()}

    assert await ids("status=draft") == {slips["draft_oct"], slips["other"]}
    assert await ids("year=2025") == {slips["paid_dec"]}
    assert await ids(f"run_id={slips['runs']['sep']}") == {slips["paid_sep"], slips["other"]}
    assert await ids(f"teacher_id={hr_world.teacher_a2.teacher_id}") == {slips["other"]}
    code = next(r["employee_code"] for r in rows if r["id"] == slips["other"])
    assert await ids(f"q={code.lower()}") == {slips["other"]}
    assert (await api.get(f"{PAYSLIPS}?status=SENT", headers=admin)).status_code == 422
    assert (await api.get(f"{PAYSLIPS}?run_id=not-a-uuid", headers=admin)).status_code == 422


async def test_run_detail_and_list_carry_names_and_counts(api, hr_world, slips):
    admin = hr_world.admin_a.headers
    detail = (await api.get(f"{RUNS}/{slips['runs']['sep']}", headers=admin)).json()
    assert {s["employee_name"] for s in detail["payslips"]} == {"IT TEACHER"}
    assert all(s["run_label"] == "Sep 2026" for s in detail["payslips"])

    runs = {r["id"]: r for r in (await api.get(RUNS, headers=admin)).json()}
    assert (runs[slips["runs"]["sep"]]["payslip_count"], runs[slips["runs"]["sep"]]["paid_count"]) == (2, 1)
    assert (runs[slips["runs"]["oct"]]["payslip_count"], runs[slips["runs"]["oct"]]["paid_count"]) == (1, 0)
    await _set_status(api, admin, slips["draft_oct"], "CANCELLED")
    runs = {r["id"]: r for r in (await api.get(RUNS, headers=admin)).json()}
    assert runs[slips["runs"]["oct"]]["payslip_count"] == 0


async def test_reading_payslips_does_not_change_payroll_values(api, hr_world, slips):
    admin = hr_world.admin_a.headers
    before = (await api.get(PAYSLIPS, headers=admin)).json()
    await api.get(f"{PAYSLIPS}/me", headers=hr_world.teacher_a.headers)
    await api.get(f"{PAYSLIPS}/{slips['paid_sep']}/pdf", headers=hr_world.teacher_a.headers)
    await api.get(f"{PAYSLIPS}/{slips['draft_oct']}/pdf", headers=admin)
    after = (await api.get(PAYSLIPS, headers=admin)).json()
    assert before == after
