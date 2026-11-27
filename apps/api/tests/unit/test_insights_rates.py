"""Null-safe insight rates and report catalog/openapi contracts."""

from datetime import date

from fastapi.testclient import TestClient

from src.main import app
from src.modules.analytics.rates import (
    FOCUS_THRESHOLD,
    attendance_rate,
    iso_week_label,
    kpi_delta,
    optional_rate,
    previous_period,
    score_band,
    term_bounds,
    working_dates,
)
from src.modules.reports.schemas import REPORT_CATALOG

client = TestClient(app)


def test_optional_rate_is_null_when_unmarked():
    assert optional_rate(0, 0) is None
    assert optional_rate(5, 0) is None
    assert optional_rate(None, 10) is None
    assert optional_rate(3, 4) == 75.0


def test_attendance_rate_never_treats_unmarked_as_present():
    # Nothing marked → Not marked, not 0% and not 100%.
    assert attendance_rate(attending=0, marked=0, expected=40) is None
    # 20 of 20 marked present, but 20 expected slots unmarked → 50%, not 100%.
    assert attendance_rate(attending=20, marked=20, expected=40) == 50.0
    # All expected slots marked present → 100%.
    assert attendance_rate(attending=40, marked=40, expected=40) == 100.0
    # Fallback to marked-only when expected is unknown.
    assert attendance_rate(attending=9, marked=10, expected=None) == 90.0


def test_score_band_and_focus_threshold():
    assert score_band(None) == "no-data"
    assert score_band(FOCUS_THRESHOLD - 1) == "needs-work"
    assert score_band(FOCUS_THRESHOLD) == "ok"
    assert score_band(75) == "strong"


def test_working_dates_skip_weekends_and_future(monkeypatch):
    days = working_dates(date(2026, 11, 2), date(2026, 11, 8), today=date(2026, 11, 6))
    # Mon 2 – Fri 6 (Sat/Sun skipped, Sat 7 and Sun 8 also future/weekend)
    assert days[0] == date(2026, 11, 2)
    assert days[-1] == date(2026, 11, 6)
    assert all(d.weekday() < 5 for d in days)


def test_previous_period_and_terms():
    prev = previous_period(date(2026, 10, 1), date(2026, 10, 14))
    assert prev == (date(2026, 9, 17), date(2026, 9, 30))
    start, end = term_bounds(date(2026, 6, 1), date(2026, 6, 30), "1")
    assert start == date(2026, 6, 1)
    start2, end2 = term_bounds(date(2026, 6, 1), date(2026, 6, 30), "2")
    assert end2 == date(2026, 6, 30)
    assert kpi_delta(80, 70) == 10
    assert kpi_delta(None, 70) is None
    assert iso_week_label(date(2026, 11, 2)).startswith("2026-W")


def test_insights_and_history_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/analytics/insights/dashboard",
        "/api/v1/analytics/insights/progress",
        "/api/v1/analytics/insights/progress/students/{student_id}",
        "/api/v1/analytics/insights/heatmap",
        "/api/v1/analytics/insights/heatmap/cell",
        "/api/v1/reports/history",
    ):
        assert p in paths, f"missing {p}"


def test_report_catalog_includes_insights_types():
    ids = {c["id"] for c in REPORT_CATALOG}
    assert "attendance" in ids
    assert "academic_performance" in ids
    assert "report_card" in ids
    assert "class_summary" in ids
    assert "fee_collection" in ids
