from datetime import date, datetime, timezone

from src.modules.fees.reminder_plan import clean_offsets, in_send_window, reminder_kind, school_now


def test_defaults_seven_and_two_days():
    due = [7, 2]
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 1), due, []) == ("DUE_7", "fee_due")
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 6), due, []) == ("DUE_2", "fee_due")
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 5), due, []) is None
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 9), due, []) is None  # overdue off by default


def test_per_school_offsets_and_overdue():
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 5), [3], []) == ("DUE_3", "fee_due")
    assert reminder_kind(date(2026, 11, 8), date(2026, 11, 11), [], [3]) == ("OVERDUE_3", "fee_overdue")


def test_clean_offsets():
    assert clean_offsets(None, (7, 2)) == [7, 2]
    assert clean_offsets([2, 2, 7, -1], (7, 2)) == [2, 7]
    assert clean_offsets([], (7, 2)) == []


def test_school_local_day_and_window():
    # 20:00 UTC on 1 Nov is already 1:30 on 2 Nov in India: the school day is the 2nd.
    now = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
    local = school_now("Asia/Kolkata", now)
    assert local.date() == date(2026, 11, 2)
    assert not in_send_window(local)
    assert in_send_window(school_now("Asia/Kolkata", datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)))
    assert school_now("Not/AZone", now).tzinfo is not None
