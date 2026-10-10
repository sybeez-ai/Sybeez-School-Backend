"""HRM Phase 1: payslip validation rules and the database constraints behind them."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.models import PayrollPayslip

RUNS = "/api/v1/hr/payroll/runs"
PAYSLIPS = "/api/v1/hr/payroll/payslips"


async def _run(api, admin):
    r = await api.post(
        RUNS,
        json={"label": "IT Nov 2026", "period_start": "2026-11-01", "period_end": "2026-11-30"},
        headers=admin.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _slip(api, admin, run_id, teacher_id, gross="40000.00", deductions="2000.00"):
    return await api.post(
        f"{RUNS}/{run_id}/payslips",
        json={"teacher_id": str(teacher_id), "gross_amount": gross, "deductions_amount": deductions},
        headers=admin.headers,
    )


async def _slips_in_run(api, admin, run_id):
    r = await api.get(f"{RUNS}/{run_id}", headers=admin.headers)
    assert r.status_code == 200, r.text
    return r.json()["payslips"]


# ── Create validation ───────────────────────────────────────


async def test_unknown_employee_is_404_not_500(api, hr_world):
    run = await _run(api, hr_world.admin_a)

    r = await _slip(api, hr_world.admin_a, run["id"], uuid4())

    assert r.status_code == 404, r.text
    assert r.json()["error"]["message"] == "Employee not found"


async def test_employee_from_another_school_is_rejected(api, hr_world):
    run = await _run(api, hr_world.admin_a)

    r = await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_b.teacher_id)

    assert r.status_code == 404, r.text
    assert await _slips_in_run(api, hr_world.admin_a, run["id"]) == []


async def test_deductions_above_gross_are_rejected(api, hr_world):
    run = await _run(api, hr_world.admin_a)

    r = await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id, gross="1.00", deductions="5.00")

    assert r.status_code == 422, r.text
    assert await _slips_in_run(api, hr_world.admin_a, run["id"]) == []


@pytest.mark.parametrize("field", ["gross_amount", "deductions_amount"])
async def test_negative_amounts_are_rejected_on_create(api, hr_world, field):
    run = await _run(api, hr_world.admin_a)
    body = {"teacher_id": str(hr_world.teacher_a.teacher_id), "gross_amount": "100.00", field: "-1"}

    r = await api.post(f"{RUNS}/{run['id']}/payslips", json=body, headers=hr_world.admin_a.headers)

    assert r.status_code == 422, r.text


async def test_duplicate_payslip_in_same_run_is_409(api, hr_world):
    run = await _run(api, hr_world.admin_a)
    first = await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id)
    assert first.status_code == 201, first.text

    r = await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id)

    assert r.status_code == 409, r.text
    assert len(await _slips_in_run(api, hr_world.admin_a, run["id"])) == 1


async def test_same_employee_allowed_in_different_runs_and_after_cancellation(api, hr_world):
    run1 = await _run(api, hr_world.admin_a)
    run2 = await _run(api, hr_world.admin_a)
    first = (await _slip(api, hr_world.admin_a, run1["id"], hr_world.teacher_a.teacher_id)).json()

    assert (await _slip(api, hr_world.admin_a, run2["id"], hr_world.teacher_a.teacher_id)).status_code == 201

    cancel = await api.patch(f"{PAYSLIPS}/{first['id']}", json={"status": "CANCELLED"}, headers=hr_world.admin_a.headers)
    assert cancel.status_code == 200, cancel.text
    reissue = await _slip(api, hr_world.admin_a, run1["id"], hr_world.teacher_a.teacher_id)
    assert reissue.status_code == 201, reissue.text

    revive = await api.patch(f"{PAYSLIPS}/{first['id']}", json={"status": "DRAFT"}, headers=hr_world.admin_a.headers)
    assert revive.status_code == 409, revive.text


# ── Update validation ───────────────────────────────────────


async def test_unknown_status_is_422(api, hr_world):
    run = await _run(api, hr_world.admin_a)
    slip = (await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id)).json()

    r = await api.patch(f"{PAYSLIPS}/{slip['id']}", json={"status": "BOGUS"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 422, r.text
    assert (await _slips_in_run(api, hr_world.admin_a, run["id"]))[0]["status"] == "DRAFT"


async def test_update_making_net_negative_is_422(api, hr_world):
    run = await _run(api, hr_world.admin_a)
    slip = (await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id)).json()

    r = await api.patch(
        f"{PAYSLIPS}/{slip['id']}", json={"deductions_amount": "40000.01"}, headers=hr_world.admin_a.headers
    )

    assert r.status_code == 422, r.text
    stored = (await _slips_in_run(api, hr_world.admin_a, run["id"]))[0]
    assert Decimal(stored["net_amount"]) == Decimal("38000.00")


async def test_mark_paid_without_date_defaults_paid_on_to_today(api, hr_world):
    run = await _run(api, hr_world.admin_a)
    slip = (await _slip(api, hr_world.admin_a, run["id"], hr_world.teacher_a.teacher_id)).json()

    r = await api.patch(f"{PAYSLIPS}/{slip['id']}", json={"status": "paid"}, headers=hr_world.admin_a.headers)

    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["paid_on"]) == ("PAID", date.today().isoformat())


# ── Database constraints (defence in depth, bypassing the service) ──


def _raw_slip(world, run_id, **overrides):
    values = {
        "id": uuid4(),
        "school_id": world.admin_a.school_id,
        "payroll_run_id": run_id,
        "teacher_id": world.teacher_a.teacher_id,
        "gross_amount": Decimal("100.00"),
        "deductions_amount": Decimal("0.00"),
        "net_amount": Decimal("100.00"),
        "status": "DRAFT",
    }
    values.update(overrides)
    return PayrollPayslip(**values)


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"net_amount": Decimal("-1.00")}, "ck_payroll_payslips_net_non_negative"),
        ({"gross_amount": Decimal("-1.00")}, "ck_payroll_payslips_gross_non_negative"),
        ({"status": "BOGUS"}, "ck_payroll_payslips_status_allowed"),
        ({"status": "PAID"}, "ck_payroll_payslips_paid_has_date"),
        ("other_school_teacher", "fk_payroll_payslips_teacher_same_school"),
        ("other_school_payslip", "fk_payroll_payslips_run_same_school"),
    ],
)
async def test_database_rejects_invalid_payslips(api, db_session, hr_world, overrides, constraint):
    run = await _run(api, hr_world.admin_a)
    if overrides == "other_school_teacher":
        overrides = {"teacher_id": hr_world.teacher_b.teacher_id}
    elif overrides == "other_school_payslip":
        overrides = {"school_id": hr_world.admin_b.school_id, "teacher_id": hr_world.teacher_b.teacher_id}

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            db_session.add(_raw_slip(hr_world, run["id"], **overrides))
            await db_session.flush()

    assert constraint in str(exc.value.orig)


async def test_database_rejects_duplicate_active_payslip(api, db_session, hr_world):
    run = await _run(api, hr_world.admin_a)
    db_session.add(_raw_slip(hr_world, run["id"]))
    await db_session.flush()

    with pytest.raises(IntegrityError) as exc:
        async with db_session.begin_nested():
            db_session.add(_raw_slip(hr_world, run["id"]))
            await db_session.flush()

    assert "uq_payroll_payslips_run_teacher_active" in str(exc.value.orig)
