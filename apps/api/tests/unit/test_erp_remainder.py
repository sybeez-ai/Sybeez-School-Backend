"""Books balance, Tally names, 80C year, unmarked pay, and test-mode Razorpay."""

from datetime import date
from decimal import Decimal

import pytest

from src.modules.fees.exports import (
    DEFAULT_80C,
    financial_year_label,
    ledger_name,
    render_80c,
    tally_envelope,
    tally_voucher_xml,
)
from src.modules.fees.gateways.razorpay import RazorpayGateway
from src.modules.finance.books import balanced
from src.modules.payroll.calc import DayMark, lop_audit
from src.core.exceptions import AppError


def test_journal_must_balance():
    assert balanced([(Decimal("10"), Decimal("0")), (Decimal("0"), Decimal("10"))])
    assert not balanced([(Decimal("10"), Decimal("0")), (Decimal("0"), Decimal("9"))])
    assert not balanced([(Decimal("0"), Decimal("0")), (Decimal("0"), Decimal("0"))])


def test_tally_default_ledgers_and_xml():
    assert ledger_name({}, "TUITION") == "Tuition Fees"
    assert ledger_name({"TUITION": "School Fees"}, "tuition") == "School Fees"
    xml = tally_envelope([
        tally_voucher_xml(
            day=date(2026, 4, 2),
            number="R1",
            debit_ledger="Cash",
            credit_ledger="Tuition Fees",
            amount=Decimal("100"),
        )
    ])
    assert "<LEDGERNAME>Cash</LEDGERNAME>" in xml
    assert "<LEDGERNAME>Tuition Fees</LEDGERNAME>" in xml
    assert "100.00" in xml


def test_indian_financial_year_and_80c_wording():
    assert financial_year_label(date(2026, 4, 1), 4) == "2026-27"
    assert financial_year_label(date(2026, 3, 31), 4) == "2025-26"
    text = render_80c("", {"student_name": "Asha", "amount": "500.00", "financial_year": "2026-27", "school_name": "Demo", "parent_name": "Ravi", "admission_no": "1"})
    assert "Asha" in text and "80C" in DEFAULT_80C


def test_unmarked_day_is_flagged_and_not_deducted():
    days = [date(2026, 10, 6)]
    rows = lop_audit([], days)
    assert rows[0]["source"] == "UNMARKED_NOT_DEDUCTED"
    assert rows[0]["lop_days"] == "0"
    assert rows[0]["flagged"] is True
    marked = lop_audit([DayMark(date(2026, 10, 6), "ABSENT")], days)
    assert marked[0]["lop_days"] == "1"


@pytest.mark.asyncio
async def test_live_razorpay_key_is_refused_and_missing_key_is_a_test_order():
    live = RazorpayGateway()
    live.key_id = "rzp_live_secret"
    live.key_secret = "secret"
    with pytest.raises(AppError, match="test mode"):
        await live.create_order(amount=Decimal("10"), currency="INR", receipt="r")
    blank = RazorpayGateway()
    blank.key_id = ""
    blank.key_secret = ""
    order = await blank.create_order(amount=Decimal("10"), currency="INR", receipt="r", transfers=[{"account": "acc"}])
    assert order.order_id.startswith("order_test_")
    assert order.meta["transfers"] == []
    checked = await blank.verify_payment(order_id=order.order_id, payment_id="pay_test", signature="test_ok", amount=Decimal("10"), currency="INR")
    assert checked.success is True
