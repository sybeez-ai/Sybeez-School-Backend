"""Pure helpers for fee reminders: per-school offsets, school-local dates, send window."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_DUE_DAYS = (7, 2)
DEFAULT_OVERDUE_DAYS: tuple[int, ...] = ()
SEND_FROM_HOUR = 9  # school-local; parents are not nudged before 9am
SEND_UNTIL_HOUR = 20


def clean_offsets(values, default: tuple[int, ...]) -> list[int]:
    """Positive, de-duplicated offsets in days. Falls back to the default when unset."""
    if values is None:
        return list(default)
    out = sorted({int(v) for v in values if isinstance(v, (int, str)) and str(v).lstrip("-").isdigit() and int(v) >= 0})
    return out


def school_now(tz_name: str | None, now: datetime | None = None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(tz_name or "Asia/Kolkata")
    except Exception:
        zone = ZoneInfo("Asia/Kolkata")
    return moment.astimezone(zone)


def in_send_window(local_now: datetime) -> bool:
    return SEND_FROM_HOUR <= local_now.hour < SEND_UNTIL_HOUR


def reminder_kind(due_date: date, today: date, due_days: list[int], overdue_days: list[int]) -> tuple[str, str] | None:
    """Return (kind, event_key) when this invoice is due a nudge today, else None."""
    days_until = (due_date - today).days
    if days_until >= 0 and days_until in due_days:
        return f"DUE_{days_until}", "fee_due"
    if days_until < 0 and -days_until in overdue_days:
        return f"OVERDUE_{-days_until}", "fee_overdue"
    return None
