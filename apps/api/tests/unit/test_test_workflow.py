from datetime import datetime, timedelta, timezone

from src.shared.test_workflow import (
    WORKFLOW_COMPLETED,
    WORKFLOW_DRAFT,
    WORKFLOW_LIVE,
    WORKFLOW_PENDING,
    WORKFLOW_SCHEDULED,
    workflow_label,
    workflow_status,
)


def test_draft_and_pending():
    assert workflow_status(approval_status="DRAFT", record_status="DRAFT") == WORKFLOW_DRAFT
    assert workflow_status(approval_status="PENDING_APPROVAL") == WORKFLOW_PENDING
    assert workflow_label(WORKFLOW_PENDING) == "Pending approval"


def test_scheduled_live_completed():
    now = datetime(2026, 11, 27, 10, 0, tzinfo=timezone.utc)
    start = now + timedelta(hours=1)
    end = now + timedelta(hours=2)
    assert (
        workflow_status(approval_status="APPROVED", starts_at=start, ends_at=end, record_status="SCHEDULED", now=now)
        == WORKFLOW_SCHEDULED
    )
    assert (
        workflow_status(
            approval_status="APPROVED",
            starts_at=now - timedelta(minutes=5),
            ends_at=end,
            record_status="SCHEDULED",
            now=now,
        )
        == WORKFLOW_LIVE
    )
    assert (
        workflow_status(
            approval_status="APPROVED",
            starts_at=now - timedelta(hours=3),
            ends_at=now - timedelta(minutes=1),
            record_status="SCHEDULED",
            now=now,
        )
        == WORKFLOW_COMPLETED
    )
    assert workflow_status(approval_status="APPROVED", record_status="PUBLISHED", now=now) == WORKFLOW_COMPLETED
