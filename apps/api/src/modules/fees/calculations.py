"""Money helpers and late-fee calculations."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

TWOPLACES = Decimal("0.01")


def money(value: Decimal | float | int | str | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def calculate_late_fee(
    *,
    balance: Decimal,
    due_date: date,
    as_of: date,
    grace_days: int,
    calculation_type: str,
    amount: Decimal,
    maximum_amount: Decimal | None,
    slabs: list | None = None,
) -> Decimal:
    """Return late fee amount (0 if not applicable)."""
    balance = money(balance)
    if balance <= 0:
        return money(0)
    start = due_date + timedelta(days=max(grace_days, 0))
    # Paying on the due date (or within the grace window) is not late.
    if as_of <= start:
        return money(0)
    days = (as_of - start).days
    calc = (calculation_type or "FIXED").upper()
    if calc == "FIXED":
        fee = money(amount)
    elif calc == "PERCENTAGE":
        fee = money(balance * money(amount) / Decimal("100"))
    elif calc == "PER_DAY":
        fee = money(money(amount) * days)
    elif calc == "SLAB":
        chosen = None
        ordered = sorted(slabs or [], key=lambda s: int(s.get("upto_days") or 0))
        for slab in ordered:
            if days <= int(slab.get("upto_days") or 0):
                chosen = slab
                break
        if chosen is None and ordered:
            chosen = ordered[-1]
        fee = money(chosen.get("amount")) if chosen else money(0)
    else:
        fee = money(0)
    if maximum_amount is not None and fee > money(maximum_amount):
        fee = money(maximum_amount)
    return fee


def invoice_status_after_payment(
    *, total: Decimal, paid: Decimal, due_date: date, as_of: date | None = None
) -> str:
    total = money(total)
    paid = money(paid)
    today = as_of or date.today()
    if total <= 0:
        # Fully concessed / waived invoices have nothing outstanding.
        return "PAID"
    if paid <= 0:
        if today > due_date:
            return "OVERDUE"
        return "ISSUED"
    if paid >= total:
        return "PAID"
    if today > due_date:
        return "OVERDUE"
    return "PARTIALLY_PAID"


def resolve_invoice_due_date(installment, installments: list, invoice_date: date) -> date:
    """Due date for a generated invoice when the caller did not pass one.

    Uses the chosen instalment's date; otherwise the structure's next instalment on or
    after the invoice date (e.g. Term 1 = 15 Nov), then its last instalment. Falls back
    to the invoice date only when the structure has no instalments.
    """
    if installment is not None and getattr(installment, "due_date", None):
        return installment.due_date
    dates = sorted(d for d in (getattr(i, "due_date", None) for i in installments or []) if d)
    if not dates:
        return invoice_date
    upcoming = [d for d in dates if d >= invoice_date]
    return upcoming[0] if upcoming else dates[-1]
