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
) -> Decimal:
    """Return late fee amount (0 if not applicable)."""
    balance = money(balance)
    if balance <= 0:
        return money(0)
    start = due_date + timedelta(days=max(grace_days, 0))
    if as_of < start:
        return money(0)
    days = (as_of - start).days + 1
    calc = (calculation_type or "FIXED").upper()
    if calc == "FIXED":
        fee = money(amount)
    elif calc == "PERCENTAGE":
        fee = money(balance * money(amount) / Decimal("100"))
    elif calc == "PER_DAY":
        fee = money(money(amount) * days)
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
    if paid <= 0:
        if today > due_date:
            return "OVERDUE"
        return "ISSUED"
    if paid >= total:
        return "PAID"
    if today > due_date:
        return "OVERDUE"
    return "PARTIALLY_PAID"
