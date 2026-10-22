from datetime import datetime, timezone

from src.modules.parent_messages.daily_summary import compose, in_summary_window, REVIEWED
from src.modules.fees.reminder_plan import school_now


def test_nothing_to_report_sends_nothing():
    assert compose("en", "Asha", None, 0, 0, 0) is None


def test_hindi_and_tamil_use_their_own_phrases():
    t, b = compose("hi", "Asha", "PRESENT", 2, 1, 0)
    assert "Asha" in t and "उपस्थित" in b and "2" in b
    t, b = compose("ta", "Asha", "ABSENT", 0, 0, 1)
    assert "Asha" in t and "இன்று" in b


def test_unreviewed_language_falls_back_to_english():
    assert compose("pa", "Asha", "LATE", 0, 0, 0)[1] == "Arrived late."
    assert {"hi", "ta"} <= REVIEWED


def test_window_is_school_local_evening():
    assert in_summary_window(school_now("Asia/Kolkata", datetime(2026, 11, 2, 13, 30, tzinfo=timezone.utc)))  # 19:00 IST
    assert not in_summary_window(school_now("Asia/Kolkata", datetime(2026, 11, 2, 6, 0, tzinfo=timezone.utc)))
