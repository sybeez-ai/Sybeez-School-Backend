"""Generate, submit, approve, and publish a statutory run on real Postgres."""

from decimal import Decimal as D
from uuid import uuid4

from src.core.security import create_access_token
from src.db.models import SchoolMembership, User
from src.shared.enums import UserRole

PAYROLL = "/api/v1/payroll"


async def _approver(db, school_id):
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.test", full_name="Payroll approver")
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school_id, role=UserRole.SCHOOL_ADMIN.value))
    await db.flush()
    token = create_access_token(subject=user.id, role=UserRole.SCHOOL_ADMIN.value, school_id=school_id)
    return {"Authorization": f"Bearer {token}"}


async def test_generate_submit_approve_publishes_payslips(api, db_session, hr_world):
    admin = hr_world.admin_a
    approver = await _approver(db_session, admin.school_id)

    people = await api.get("/api/v1/hr/employees", headers=admin.headers)
    assert people.status_code == 200, people.text
    employees = people.json()
    assert employees
    employee_id = next(row["id"] for row in employees if row["teacher_id"] == str(hr_world.teacher_a.teacher_id))

    settings = await api.get(f"{PAYROLL}/settings", headers=admin.headers)
    assert settings.status_code == 200, settings.text

    templates = await api.get(f"{PAYROLL}/salary-templates", headers=admin.headers)
    assert templates.status_code == 200, templates.text
    template_id = templates.json()[0]["template"]["id"]

    for row in employees:
        assigned = await api.post(
            f"{PAYROLL}/salary-assignments",
            headers=admin.headers,
            json={
                "employee_id": row["id"],
                "template_id": template_id,
                "effective_from": "2026-10-01",
                "overrides": {},
                "reason": "October salary",
            },
        )
        assert assigned.status_code == 201, assigned.text

    generated = await api.post(
        f"{PAYROLL}/runs/generate",
        headers=admin.headers,
        json={"period_start": "2026-10-01", "period_end": "2026-10-31", "reason": "October payroll"},
    )
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["created"] is True
    assert body["skipped"] == []
    run_id = body["run"]["id"]
    assert body["run"]["approval_status"] == "DRAFT"

    submitted = await api.post(
        f"{PAYROLL}/runs/{run_id}/submit",
        headers=admin.headers,
        json={"reason": "Ready for approval"},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["approval_status"] == "PENDING_APPROVAL"

    approved = await api.post(
        f"{PAYROLL}/runs/{run_id}/approve",
        headers=approver,
        json={"reason": "Approved for publication"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "PUBLISHED"
    assert approved.json()["published_at"]

    stored = await api.get(f"{PAYROLL}/runs/{run_id}", headers=approver)
    assert stored.status_code == 200, stored.text
    payslips = stored.json()["payslips"]
    assert len(payslips) == len(employees)
    mine = next(item for item in payslips if item["payslip"]["employee_id"] == employee_id)
    assert mine["payslip"]["status"] == "PUBLISHED"
    assert mine["lines"]
    assert all(item["payslip"]["status"] == "PUBLISHED" for item in payslips)
    from sqlalchemy import select

    from src.db.models import ExpenseLedgerEntry, SchoolExpense

    voucher = (
        await db_session.execute(select(SchoolExpense).where(SchoolExpense.invoice_number == f"PAYRUN-{run_id}"))
    ).scalar_one()
    assert voucher.status == "APPROVED"
    entries = (
        await db_session.execute(select(ExpenseLedgerEntry).where(ExpenseLedgerEntry.expense_id == voucher.id))
    ).scalars().all()
    assert sum(e.debit for e in entries) == sum(e.credit for e in entries) == voucher.total_amount
    assert any(e.account == "SALARY_PAYABLE" and e.credit > 0 for e in entries)

    # Form 16 reports salary actually paid: payslip gross less loss of pay.
    lop = sum(D(line["amount"]) for line in mine["lines"] if line["code"] == "LOP")
    form16 = await api.get(f"{PAYROLL}/form16/{employee_id}?financial_year=2026-27", headers=approver)
    assert form16.status_code == 200, form16.text
    assert D(form16.json()["gross"]) == D(mine["payslip"]["gross_amount"]) - lop
    assert "pf_deducted" in form16.json()

    # Teaching staff carry their code on the teacher record; the bank file must show it.
    expected_code = next(row["employee_code"] for row in employees if row["id"] == employee_id)
    saved = await api.put(
        f"{PAYROLL}/employees/{employee_id}/bank",
        headers=admin.headers,
        json={"account_holder": "Teacher A", "account_number": "000012345678", "ifsc": "HDFC0000001", "bank_name": "HDFC", "reason": "Salary account"},
    )
    assert saved.status_code == 200, saved.text
    bank = await api.get(f"{PAYROLL}/runs/{run_id}/bank-file", headers=approver)
    assert bank.status_code == 200, bank.text
    assert expected_code
    assert expected_code in bank.text

    # The teacher sees their own Form 16 even though pay runs are not visible to them.
    own = await api.get(f"{PAYROLL}/form16/me?financial_year=2026-27", headers=hr_world.teacher_a.headers)
    assert own.status_code == 200, own.text
    assert own.json()["payslip_count"] == 1
    assert D(own.json()["gross"]) == D(form16.json()["gross"])

    # Salary vouchers are settled from payroll, not from finance expenses.
    pay = await api.post(
        f"/api/v1/finance/expenses/{voucher.id}/pay", headers=admin.headers, json={"paid_on": "2026-11-01"}
    )
    assert pay.status_code in (403, 422), pay.text
    paid = await api.patch(
        f"{PAYROLL}/payslips/{mine['payslip']['id']}/payment",
        headers=admin.headers,
        json={"status": "PAID", "utr": "UTR123456", "reason": "Paid by bank"},
    )
    assert paid.status_code == 200, paid.text
    net = D(mine["payslip"]["net_amount"])
    if net > 0:
        bank_rows = (
            await db_session.execute(
                select(ExpenseLedgerEntry).where(
                    ExpenseLedgerEntry.expense_id == voucher.id, ExpenseLedgerEntry.account == "BANK"
                )
            )
        ).scalars().all()
        assert sum(row.credit for row in bank_rows) == net
