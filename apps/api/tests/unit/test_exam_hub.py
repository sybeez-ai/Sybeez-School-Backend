from datetime import date, time
from uuid import uuid4

from src.modules.exams.hub import _countdown, _live_status, _tab_for
from src.modules.exams.schemas import ClassTestCreate, ExamHubScheduleIn
from src.shared.test_workflow import (
    WORKFLOW_COMPLETED,
    WORKFLOW_LIVE,
    WORKFLOW_PENDING,
    WORKFLOW_SCHEDULED,
    combine_start,
    ends_from_duration,
    workflow_status,
)


def test_class_test_create_accepts_class_ids():
    subject_id = uuid4()
    first = uuid4()
    second = uuid4()
    body = ClassTestCreate(
        title="Paper",
        class_id=first,
        class_ids=[first, second],
        subject_id=subject_id,
        exam_date=date(2026, 12, 1),
        start_time=time(9, 0),
        duration_minutes=60,
    )
    assert body.resolved_class_ids() == [first, second]


def test_class_test_create_requires_a_class_list_when_empty():
    body = ClassTestCreate(
        title="Paper",
        subject_id=uuid4(),
        exam_date=date(2026, 12, 1),
        start_time=time(9, 0),
        duration_minutes=60,
    )
    assert body.resolved_class_ids() == []


def test_hub_tabs_map_workflow_status():
    assert _tab_for(_live_status(WORKFLOW_PENDING)) == "pending"
    assert _tab_for(_live_status(WORKFLOW_LIVE)) == "live"
    assert _tab_for(_live_status(WORKFLOW_COMPLETED)) == "completed"
    assert _tab_for(_live_status(WORKFLOW_SCHEDULED)) == "upcoming"
    assert _tab_for(_live_status("draft")) == "upcoming"


def test_countdown_before_and_during():
    from datetime import datetime, timezone

    start = datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc)
    assert _countdown(start, end, datetime(2026, 11, 28, 8, 0, tzinfo=timezone.utc)) == 3600
    assert _countdown(start, end, datetime(2026, 11, 28, 9, 15, tzinfo=timezone.utc)) == 2700
    assert _countdown(start, end, datetime(2026, 11, 28, 10, 1, tzinfo=timezone.utc)) is None


def test_schedule_schema_defaults_to_schedule_action():
    body = ExamHubScheduleIn(
        kind="written",
        title="Paper",
        subject_id=uuid4(),
        class_ids=[uuid4()],
        exam_date=date(2026, 12, 1),
        start_time=time(9, 0),
        duration_minutes=45,
    )
    assert body.action == "schedule"
    starts = combine_start(body.exam_date, body.start_time)
    assert starts is not None
    assert ends_from_duration(starts, body.duration_minutes).hour == 9
    assert ends_from_duration(starts, body.duration_minutes).minute == 45


def test_written_exam_is_live_once_started():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 28, 9, 30, tzinfo=timezone.utc)
    assert (
        workflow_status(
            approval_status="APPROVED",
            starts_at=datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc),
            ends_at=datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc),
            record_status="SCHEDULED",
            now=now,
        )
        == WORKFLOW_LIVE
    )


def test_published_online_test_is_live_during_its_window():
    from datetime import datetime, timezone

    now = datetime(2026, 11, 27, 23, 30, tzinfo=timezone.utc)
    assert (
        workflow_status(
            approval_status="APPROVED",
            starts_at=datetime(2026, 11, 27, 23, 10, tzinfo=timezone.utc),
            ends_at=datetime(2026, 11, 28, 0, 10, tzinfo=timezone.utc),
            record_status="SCHEDULED",
            now=now,
        )
        == WORKFLOW_LIVE
    )
    assert (
        workflow_status(
            approval_status="APPROVED",
            starts_at=datetime(2026, 11, 27, 23, 10, tzinfo=timezone.utc),
            ends_at=datetime(2026, 11, 28, 0, 10, tzinfo=timezone.utc),
            record_status="PUBLISHED",
            now=now,
        )
        == WORKFLOW_COMPLETED
    )
