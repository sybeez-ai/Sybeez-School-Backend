"""Phase 11 reports & analytics — contract/unit coverage."""

from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.analytics.schemas import AnalyticsFilters, OverviewOut
from src.modules.analytics.service import _pct
from src.modules.reports.exporters import rows_to_csv, rows_to_pdf
from src.modules.reports.schemas import REPORT_CATALOG, ReportGenerateIn, SavedReportCreate

client = TestClient(app)


def test_analytics_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/analytics/overview",
        "/api/v1/analytics/academic",
        "/api/v1/analytics/students",
        "/api/v1/analytics/attendance",
        "/api/v1/analytics/homework",
        "/api/v1/analytics/exams",
        "/api/v1/analytics/fees",
        "/api/v1/analytics/communication",
        "/api/v1/analytics/classes",
        "/api/v1/analytics/classes/{section_id}",
        "/api/v1/analytics/students/{student_id}",
        "/api/v1/analytics/me",
        "/api/v1/analytics/settings",
    ):
        assert p in paths, f"missing {p}"


def test_reports_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/reports",
        "/api/v1/reports/generate",
        "/api/v1/reports/export/csv",
        "/api/v1/reports/export/pdf",
        "/api/v1/reports/saved",
        "/api/v1/reports/saved/{report_id}",
    ):
        assert p in paths, f"missing {p}"


def test_analytics_and_reports_require_auth():
    assert client.get("/api/v1/analytics/overview").status_code == 401
    assert client.get("/api/v1/analytics/attendance").status_code == 401
    assert client.get("/api/v1/analytics/me").status_code == 401
    assert client.get("/api/v1/reports").status_code == 401
    assert client.post("/api/v1/reports/generate", json={}).status_code == 401
    assert client.post("/api/v1/reports/export/csv", json={}).status_code == 401


def test_pct_helper():
    assert _pct(93, 100) == 93.0
    assert _pct(0, 0) == 0.0
    assert _pct(1, 3) == 33.3


def test_report_catalog_covers_domains():
    cats = {c["category"] for c in REPORT_CATALOG}
    assert "Academic" in cats
    assert "Attendance" in cats
    assert "Homework" in cats
    assert "Exams" in cats
    assert "Fees" in cats


def test_report_generate_schema():
    body = ReportGenerateIn(
        report_type="attendance",
        academic_year_id=uuid4(),
        section_id=uuid4(),
        from_date=date(2026, 6, 1),
        to_date=date(2026, 9, 30),
        fields=["student", "status"],
    )
    assert body.report_type == "attendance"


def test_saved_report_schema():
    SavedReportCreate(name="Monthly Attendance", report_type="attendance", configuration={"grade": "8"})


def test_csv_and_pdf_exporters():
    rows = [{"student": "Rahul", "status": "PRESENT"}, {"student": "Anu", "status": "ABSENT"}]
    csv = rows_to_csv(rows, ["student", "status"])
    assert "Rahul" in csv
    assert "PRESENT" in csv
    pdf = rows_to_pdf("Attendance Report", rows, ["student", "status"])
    assert pdf.startswith(b"%PDF")


def test_filters_schema():
    f = AnalyticsFilters(academic_year_id=uuid4(), grade_id=uuid4())
    assert f.section_id is None


def test_overview_schema_shape():
    # structural check only
    assert set(OverviewOut.model_fields.keys()) >= {
        "students",
        "teachers",
        "attendance_rate",
        "homework_completion",
        "exam_average",
        "fees_collection_rate",
    }
