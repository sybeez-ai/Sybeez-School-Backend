from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.db.models.fee import FeeInvoice, StudentFeeAccount
from tests.integration.online_tests_world import build_world
from tests.integration.test_online_tests_api import api  # noqa: F401

pytestmark = pytest.mark.asyncio


async def _invoice(db, w, student, total="1500.00"):
    year = (await db.execute(text("SELECT id FROM academic_years WHERE school_id = :s"), {"s": w.school_id})).scalar_one()
    acc = StudentFeeAccount(id=uuid4(), school_id=w.school_id, student_id=student.profile_id, academic_year_id=year, status="ACTIVE")
    db.add(acc)
    await db.flush()
    amt = Decimal(total)
    inv = FeeInvoice(id=uuid4(), school_id=w.school_id, student_fee_account_id=acc.id, invoice_number=f"INV-{uuid4().hex[:6]}",
                     invoice_date=date(2026, 10, 1), due_date=date(2026, 10, 30), currency="INR", subtotal=amt,
                     discount_amount=Decimal("0"), tax_amount=Decimal("0"), total_amount=amt, paid_amount=Decimal("0"),
                     balance_amount=amt, status="ISSUED")
    db.add(inv)
    await db.flush()
    return inv


async def test_upi_profile_and_invoice_qr(api, db_session):  # noqa: F811
    w = await build_world(db_session)
    inv = await _invoice(db_session, w, w.students[0])
    await db_session.commit()
    url = f"/api/v1/fees/invoices/{inv.id}/upi"
    # not configured yet
    assert (await api.get(url, headers=w.admin.h)).status_code == 404
    # bad inputs rejected, good saved; teachers/parents cannot edit
    bad = await api.put("/api/v1/fees/profile", json={"upi_vpa": "nope"}, headers=w.admin.h)
    assert bad.status_code == 422
    assert (await api.put("/api/v1/fees/profile", json={"gst_mode": "TAXABLE", "gst_rate": 18}, headers=w.admin.h)).status_code == 422
    for p in (w.teacher, w.parent):
        assert (await api.put("/api/v1/fees/profile", json={"upi_vpa": "s@okbank"}, headers=p.h)).status_code == 403
    ok = await api.put("/api/v1/fees/profile", json={"upi_vpa": "school@okbank", "payee_name": "Test School", "gstin": "29ABCDE1234F1Z5", "gst_mode": "TAXABLE", "gst_rate": 18}, headers=w.admin.h)
    assert ok.status_code == 200 and ok.json()["configured"] is True
    r = await api.get(url, headers=w.admin.h)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["amount"] in ("1500.00", "1500") and "am=1500.00" in j["upi_uri"] and j["reference"] == inv.invoice_number and j["qr_svg"].lstrip().startswith("<svg")
    # linked parent allowed, unlinked parent and teacher not, other school's admin not
    assert (await api.get(url, headers=w.parent.h)).status_code == 200
    assert (await api.get(url, headers=w.parent_other.h)).status_code == 403
    assert (await api.get(url, headers=w.teacher.h)).status_code == 403
    assert (await api.get(url, headers=w.other_admin.h)).status_code in (403, 404)
    assert (await api.get("/api/v1/fees/profile", headers=w.other_admin.h)).json()["configured"] is False
    n = (await db_session.execute(text("SELECT count(*) FROM audit_logs WHERE school_id = :s AND action = 'fees.profile_update'"), {"s": w.school_id})).scalar_one()
    assert n == 1
