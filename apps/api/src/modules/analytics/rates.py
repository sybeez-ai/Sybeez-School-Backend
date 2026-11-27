"""Null-safe rate helpers for school insights and analytics."""

from __future__ import annotations

from datetime import date, timedelta

# Same thresholds as intelligence/service.py — do not invent a second rule.
FOCUS_THRESHOLD = 50.0
STRENGTH_THRESHOLD = 75.0
ATTENDANCE_RISK_THRESHOLD = 80.0
DEFAULT_WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Mon–Fri
DEFAULT_LOOKBACK_DAYS = 56  # eight weeks, so charts have enough points


def optional_rate(part: float | int | None, whole: float | int | None) -> float | None:
    """Return a percentage, or None when the denominator is missing or zero."""
    if part is None or whole is None:
        return None
    if float(whole) <= 0:
        return None
    return round(float(part) / float(whole) * 100, 1)


def attendance_rate(*, attending: int, marked: int, expected: int | None) -> float | None:
    """Never treat unmarked days as present.

    If nothing has been marked, return None ("Not marked"). When an expected
    slot count is known, unmarked students/days sit in the denominator so a
    partially marked class cannot show 100%.
    """
    if marked <= 0:
        return None
    if expected is not None and expected > 0:
        return optional_rate(attending, expected)
    return optional_rate(attending, marked)


def score_band(value: float | None) -> str:
    if value is None:
        return "no-data"
    if value < FOCUS_THRESHOLD:
        return "needs-work"
    if value < STRENGTH_THRESHOLD:
        return "ok"
    return "strong"


def parse_working_days(raw: str | None) -> set[int]:
    if not raw:
        return set(DEFAULT_WEEKDAYS)
    days: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 0 <= n <= 6:
            days.add(n)
    return days or set(DEFAULT_WEEKDAYS)


def working_dates(
    start: date,
    end: date,
    weekdays: set[int] | frozenset[int] | None = None,
    *,
    today: date | None = None,
) -> list[date]:
    weekdays = set(weekdays or DEFAULT_WEEKDAYS)
    today = today or date.today()
    end = min(end, today)
    if start > end:
        return []
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() in weekdays:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def previous_period(start: date, end: date) -> tuple[date, date]:
    days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)
    return prev_start, prev_end


def term_bounds(year_start: date, year_end: date, term: str | None) -> tuple[date, date] | None:
    if not term:
        return None
    key = term.strip().lower()
    mid = year_start + (year_end - year_start) / 2
    if key in {"1", "t1", "term1", "term_1"}:
        return year_start, mid
    if key in {"2", "t2", "term2", "term_2"}:
        return mid + timedelta(days=1), year_end
    return None


def kpi_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 1)


def iso_week_label(d: date) -> str:
    iso_year, week, _ = d.isocalendar()
    return f"{iso_year}-W{week:02d}"


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())
