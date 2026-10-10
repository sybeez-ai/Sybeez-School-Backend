"""Regression: PATCH /hr/payroll/payslips/{id} (edit amounts, mark paid) returned 500 (MissingGreenlet on updated_at)."""

from datetime import datetime
from decimal import Decimal

RUNS = "/api/v1/hr/payroll/runs"
PAYSLIPS = "/api/v1/hr/payroll/payslips"


async def _draft_payslip(api, admin, teacher, gross="50000.00", deductions="5000.00"):
    r = await api.post(
        RUNS,
        json={"label": "IT Oct 2026", "period_start": "2026-10-01", "period_end": "2026-10-31"},
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    run = r.json()
    r = await api.post(
        f"{RUNS}/{run['id']}/payslips",
        json={"teacher_id": str(teacher.teacher_id), "gross_amount": gross, "deductions_amount": deductions},
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    return run, r.json()


async def _slip_in_run(api, admin, run_id, slip_id):
    r = await api.get(f"{RUNS}/{run_id}", headers=admin.headers)
    assert r.status_code == 200, r.text
    return next(s for s in r.json()["payslips"] if s["id"] == slip_id)


async def test_admin_updates_amounts_and_net_is_recomputed(api, hr_world):
    run, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(
        f"{PAYSLIPS}/{slip['id']}",
        json={"gross_amount": "52000.00", "deductions_amount": "4500.00", "notes": "Revised"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert Decimal(body["gross_amount"]) == Decimal("52000.00")
    assert Decimal(body["deductions_amount"]) == Decimal("4500.00")
    assert Decimal(body["net_amount"]) == Decimal("47500.00")
    assert body["notes"] == "Revised"
    assert body["status"] == "DRAFT"
    assert datetime.fromisoformat(body["updated_at"]) >= datetime.fromisoformat(body["created_at"])

    stored = await _slip_in_run(api, hr_world.admin_a, run["id"], slip["id"])
    assert Decimal(stored["net_amount"]) == Decimal("47500.00")


async def test_admin_marks_payslip_paid(api, hr_world):
    run, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(
        f"{PAYSLIPS}/{slip['id']}",
        json={"status": "paid", "paid_on": "2026-10-31"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PAID"
    assert r.json()["paid_on"] == "2026-10-31"
    assert Decimal(r.json()["net_amount"]) == Decimal("45000.00")

    stored = await _slip_in_run(api, hr_world.admin_a, run["id"], slip["id"])
    assert (stored["status"], stored["paid_on"]) == ("PAID", "2026-10-31")

    mine = await api.get(f"{PAYSLIPS}/me", headers=hr_world.teacher_a.headers)
    assert mine.status_code == 200, mine.text
    assert [(s["id"], s["status"]) for s in mine.json()] == [(slip["id"], "PAID")]


async def test_negative_amount_is_422_and_slip_unchanged(api, hr_world):
    run, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(f"{PAYSLIPS}/{slip['id']}", json={"gross_amount": "-1"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 422, r.text
    stored = await _slip_in_run(api, hr_world.admin_a, run["id"], slip["id"])
    assert Decimal(stored["gross_amount"]) == Decimal("50000.00")


async def test_teacher_cannot_update_own_payslip(api, hr_world):
    run, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(
        f"{PAYSLIPS}/{slip['id']}", json={"status": "PAID"}, headers=hr_world.teacher_a.headers
    )

    assert r.status_code == 403, r.text
    stored = await _slip_in_run(api, hr_world.admin_a, run["id"], slip["id"])
    assert stored["status"] == "DRAFT"


async def test_admin_of_other_school_cannot_update(api, hr_world):
    run, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(
        f"{PAYSLIPS}/{slip['id']}", json={"status": "PAID"}, headers=hr_world.admin_b.headers
    )

    assert r.status_code == 404, r.text
    stored = await _slip_in_run(api, hr_world.admin_a, run["id"], slip["id"])
    assert stored["status"] == "DRAFT"
    other = await api.get(f"{RUNS}/{run['id']}", headers=hr_world.admin_b.headers)
    assert other.status_code == 404


async def test_unauthenticated_update_is_401(api, hr_world):
    _, slip = await _draft_payslip(api, hr_world.admin_a, hr_world.teacher_a)

    r = await api.patch(f"{PAYSLIPS}/{slip['id']}", json={"status": "PAID"})

    assert r.status_code == 401, r.text


async def test_update_unknown_payslip_is_404(api, hr_world):
    r = await api.patch(
        f"{PAYSLIPS}/00000000-0000-0000-0000-000000000000",
        json={"status": "PAID"},
        headers=hr_world.admin_a.headers,
    )

    assert r.status_code == 404, r.text
