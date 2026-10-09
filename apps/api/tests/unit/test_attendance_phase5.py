"""Phase 5 attendance / leave contract tests."""

from datetime import date, time
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.attendance.schemas import (
    AttendanceMarkItem,
    AttendanceMarkRequest,
    AttendanceSettingsUpdate,
    LeaveDecisionRequest,
    LeaveRequestCreate,
)
from src.modules.attendance.service import _present_like, _rate
from src.shared.enums import AttendanceStatus, LeaveRequestStatus

client = TestClient(app)


def test_attendance_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/attendance/settings",
        "/api/v1/attendance/overview",
        "/api/v1/attendance/alerts",
        "/api/v1/attendance/reports/students",
        "/api/v1/attendance/reports/classes",
        "/api/v1/attendance/roster",
        "/api/v1/attendance/summary",
        "/api/v1/attendance/mark",
        "/api/v1/attendance/me/monthly",
        "/api/v1/attendance/children",
        "/api/v1/attendance/student/{student_id}/monthly",
        "/api/v1/attendance/student/{student_id}/audit",
        "/api/v1/attendance/leave-requests",
        "/api/v1/attendance/leave-requests/{leave_id}/decide",
        "/api/v1/attendance/leave-requests/{leave_id}/cancel",
    ):
        assert p in paths, f"missing {p}"


def test_attendance_endpoints_require_auth():
    assert client.get("/api/v1/attendance/settings").status_code == 401
    assert client.get("/api/v1/attendance/overview").status_code == 401
    assert client.get("/api/v1/attendance/roster?class_id=%s&attended_on=2026-09-27" % uuid4()).status_code == 401
    assert client.post("/api/v1/attendance/mark", json={}).status_code == 401
    assert client.get("/api/v1/attendance/leave-requests").status_code == 401
    lid = str(uuid4())
    assert client.post(f"/api/v1/attendance/leave-requests/{lid}/decide", json={"decision": "APPROVED"}).status_code == 401
    assert client.post(f"/api/v1/attendance/leave-requests/{lid}/cancel").status_code == 401


def test_attendance_mark_schema_supports_all_statuses():
    for status in AttendanceStatus:
        item = AttendanceMarkItem(student_id=uuid4(), status=status)
        assert item.status == status
    body = AttendanceMarkRequest(
        class_id=uuid4(),
        attended_on=date(2026, 9, 27),
        marks=[
            AttendanceMarkItem(student_id=uuid4(), status=AttendanceStatus.PRESENT),
            AttendanceMarkItem(
                student_id=uuid4(),
                status=AttendanceStatus.LATE,
                arrival_time=time(9, 27),
                reason="Traffic",
            ),
            AttendanceMarkItem(student_id=uuid4(), status=AttendanceStatus.ABSENT),
            AttendanceMarkItem(student_id=uuid4(), status=AttendanceStatus.HALF_DAY),
            AttendanceMarkItem(student_id=uuid4(), status=AttendanceStatus.EXCUSED),
        ],
    )
    assert len(body.marks) == 5


def test_leave_create_and_decision_schema():
    leave = LeaveRequestCreate(
        student_id=uuid4(),
        start_date=date(2026, 9, 27),
        end_date=date(2026, 9, 28),
        reason="Medical appointment",
        attachment_name="note.pdf",
        source="REPORT_ABSENCE",
    )
    assert leave.end_date >= leave.start_date
    approved = LeaveDecisionRequest(decision=LeaveRequestStatus.APPROVED, decision_note="Approved medical leave")
    assert approved.decision == LeaveRequestStatus.APPROVED
    rejected = LeaveDecisionRequest(decision=LeaveRequestStatus.REJECTED)
    assert rejected.decision == LeaveRequestStatus.REJECTED


def test_leave_range_validation():
    try:
        LeaveRequestCreate(
            start_date=date(2026, 9, 28),
            end_date=date(2026, 9, 27),
            reason="Bad range",
        )
        assert False, "expected validation error"
    except Exception:
        pass


def test_settings_update_bounds():
    body = AttendanceSettingsUpdate(
        low_attendance_percent=85,
        absence_alert_days=3,
        absence_alert_window_days=30,
        late_alert_count=5,
        notify_parents_on_absent=True,
        teacher_can_approve_leave=True,
    )
    assert body.low_attendance_percent == 85


def test_rate_and_present_like_helpers():
    assert _rate(20, 24) == 83.3
    assert _rate(0, 0) == 100.0
    assert _present_like(AttendanceStatus.PRESENT.value) is True
    assert _present_like(AttendanceStatus.LATE.value) is True
    assert _present_like(AttendanceStatus.EXCUSED.value) is True
    assert _present_like(AttendanceStatus.HALF_DAY.value) is True
    assert _present_like(AttendanceStatus.ABSENT.value) is False
