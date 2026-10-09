"""Phase 6 timetable contract tests."""

from datetime import time
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app
from src.modules.timetable.schemas import (
    PeriodCreate,
    TimetableEntryCreate,
    TimetableSettingsUpdate,
)
from src.modules.timetable.service import _parse_working_days, _times_overlap
from src.shared.enums import PeriodKind

client = TestClient(app)


def test_timetable_openapi_paths():
    paths = client.get("/openapi.json").json()["paths"]
    for p in (
        "/api/v1/timetable/settings",
        "/api/v1/timetable/periods",
        "/api/v1/timetable/periods/{period_id}",
        "/api/v1/timetable/periods/reorder",
        "/api/v1/timetable/rooms",
        "/api/v1/timetable/rooms/{room_id}",
        "/api/v1/timetable",
        "/api/v1/timetable/{entry_id}",
        "/api/v1/timetable/mine",
        "/api/v1/timetable/teacher",
        "/api/v1/timetable/student",
        "/api/v1/timetable/parent",
        "/api/v1/timetable/parent/children",
        "/api/v1/timetable/today",
        "/api/v1/timetable/grid",
    ):
        assert p in paths, f"missing {p}"


def test_timetable_endpoints_require_auth():
    assert client.get("/api/v1/timetable/settings").status_code == 401
    assert client.get("/api/v1/timetable/periods").status_code == 401
    assert client.get("/api/v1/timetable/mine").status_code == 401
    assert client.get("/api/v1/timetable/student").status_code == 401
    assert client.get("/api/v1/timetable/today").status_code == 401
    assert client.post("/api/v1/timetable", json={}).status_code == 401
    eid = str(uuid4())
    assert client.patch(f"/api/v1/timetable/{eid}", json={}).status_code == 401
    assert client.delete(f"/api/v1/timetable/{eid}").status_code == 401


def test_period_create_schema():
    body = PeriodCreate(
        name="Period 1",
        kind=PeriodKind.LESSON,
        starts_at=time(9, 0),
        ends_at=time(9, 45),
    )
    assert body.kind == PeriodKind.LESSON
    break_p = PeriodCreate(
        name="Break",
        kind=PeriodKind.BREAK,
        starts_at=time(10, 30),
        ends_at=time(10, 45),
    )
    assert break_p.kind == PeriodKind.BREAK


def test_period_requires_end_after_start():
    try:
        PeriodCreate(
            name="Bad",
            kind=PeriodKind.LESSON,
            starts_at=time(10, 0),
            ends_at=time(9, 0),
        )
        assert False, "expected validation error"
    except Exception:
        pass


def test_entry_create_requires_period_or_times():
    try:
        TimetableEntryCreate(
            class_id=uuid4(),
            subject_id=uuid4(),
            day_of_week=0,
        )
        assert False, "expected validation error"
    except Exception:
        pass

    ok = TimetableEntryCreate(
        class_id=uuid4(),
        subject_id=uuid4(),
        day_of_week=0,
        period_id=uuid4(),
    )
    assert ok.period_id is not None


def test_settings_working_days():
    body = TimetableSettingsUpdate(working_days=[0, 1, 2, 3, 4])
    assert body.working_days == [0, 1, 2, 3, 4]
    assert _parse_working_days("0,1,2,3,4") == [0, 1, 2, 3, 4]
    assert _parse_working_days("") == [0, 1, 2, 3, 4]


def test_times_overlap_helper():
    assert _times_overlap(time(9, 0), time(9, 45), time(9, 30), time(10, 15)) is True
    assert _times_overlap(time(9, 0), time(9, 45), time(9, 45), time(10, 30)) is False
    assert _times_overlap(time(9, 0), time(9, 45), time(10, 0), time(10, 45)) is False
