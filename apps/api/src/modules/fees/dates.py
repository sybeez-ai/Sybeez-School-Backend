"""Calendar days for fee totals. Schools in this product run on India time."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def ist_today() -> date:
    return datetime.now(IST).date()


def paid_on(moment: datetime | None) -> date | None:
    """The India calendar day of a payment. Naive timestamps are treated as UTC."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(IST).date()
