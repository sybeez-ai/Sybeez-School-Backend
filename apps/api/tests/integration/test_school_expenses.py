"""Vendor expenses: draft, a second person approves, payment lands on the day book."""

from datetime import date
from uuid import uuid4

from src.core.security import create_access_token
from src.db.models import SchoolMembership, User
from src.shared.enums import UserRole

FINANCE = "/api/v1/finance"


async def _accountant(db, school_id):
    user = User(id=uuid4(), email=f"it-{uuid4().hex[:12]}@example.test", full_name="School accountant")
    db.add(user)
    await db.flush()
    db.add(SchoolMembership(user_id=user.id, school_id=school_id, role=UserRole.ACCOUNTANT.value))
    await db.flush()
    token = create_access_token(subject=user.id, role=UserRole.ACCOUNTANT.value, school_id=school_id)
    return {"Authorization": f"Bearer {token}"}


async def test_expense_approval_posts_the_day_book(api, db_session, hr_world):
    admin = hr_world.admin_a.headers
    head = await api.post(
        f"{FINANCE}/expense-heads",
        headers=admin,
        json={"code": "power", "name": "Electricity"},
    )
    assert head.status_code == 201, head.text
    assert head.json()["code"] == "POWER"

    drafted = await api.post(
        f"{FINANCE}/expenses",
        headers=admin,
        json={
            "head_id": head.json()["id"],
            "vendor_name": "City Power",
            "vendor_gstin": "29ABCDE1234F1Z5",
            "invoice_number": "INV-19",
            "invoice_date": "2026-10-11",
            "taxable_amount": "1000.00",
            "gst_rate": "18",
            "payment_mode": "BANK",
        },
    )
    assert drafted.status_code == 201, drafted.text
    expense = drafted.json()
    assert expense["status"] == "DRAFT"
    assert expense["gst_amount"] == "180.00"
    assert expense["total_amount"] == "1180.00"
    assert expense["voucher_no"] == "EXP-00001"

    submitted = await api.post(f"{FINANCE}/expenses/{expense['id']}/submit", headers=admin)
    assert submitted.status_code == 200, submitted.text
    self_approve = await api.post(
        f"{FINANCE}/expenses/{expense['id']}/approve",
        headers=admin,
        json={"note": "Looks fine"},
    )
    assert self_approve.status_code == 403

    accountant = await _accountant(db_session, hr_world.admin_a.school_id)
    approved = await api.post(
        f"{FINANCE}/expenses/{expense['id']}/approve",
        headers=accountant,
        json={"note": "Approved"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"

    book = await api.get(
        f"{FINANCE}/reports/day-book",
        headers=admin,
        params={"on": "2026-10-11"},
    )
    assert book.status_code == 200, book.text
    rows = book.json()
    match = [row for row in rows if row.get("kind") == "expense" and row.get("receipt") == "EXP-00001"]
    assert match
    assert match[0]["student"] == "City Power"
    assert match[0]["amount"] == "1180.00"

    paid = await api.post(
        f"{FINANCE}/expenses/{expense['id']}/pay",
        headers=admin,
        json={"paid_on": "2026-10-12"},
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == "PAID"
    paid_book = await api.get(
        f"{FINANCE}/reports/day-book",
        headers=admin,
        params={"on": "2026-10-12"},
    )
    assert any(row.get("receipt") == "EXP-00001" and row.get("status") == "PAID" for row in paid_book.json())

    other = await api.get(f"{FINANCE}/expenses", headers=hr_world.admin_b.headers)
    assert other.status_code == 200, other.text
    assert other.json() == []
    assert date.today()
