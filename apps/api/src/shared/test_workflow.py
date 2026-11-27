"""Shared status labels for written exams and online tests."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

APPROVAL_DRAFT = "DRAFT"
APPROVAL_PENDING = "PENDING_APPROVAL"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"

WORKFLOW_DRAFT = "draft"
WORKFLOW_PENDING = "pending_approval"
WORKFLOW_SCHEDULED = "approved_scheduled"
WORKFLOW_LIVE = "live"
WORKFLOW_COMPLETED = "completed"
WORKFLOW_REJECTED = "rejected"

_LABELS = {
    WORKFLOW_DRAFT: "Draft",
    WORKFLOW_PENDING: "Pending approval",
    WORKFLOW_SCHEDULED: "Approved / scheduled",
    WORKFLOW_LIVE: "Live",
    WORKFLOW_COMPLETED: "Completed",
    WORKFLOW_REJECTED: "Rejected",
}


def workflow_label(status: str) -> str:
    return _LABELS.get(status, status.replace("_", " ").title())


def _as_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def combine_start(exam_date: date | None, starts_at: time | None) -> datetime | None:
    if exam_date is None:
        return None
    clock = starts_at or time(0, 0)
    return datetime.combine(exam_date, clock, tzinfo=timezone.utc)


def workflow_status(
    *,
    approval_status: str | None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    record_status: str | None = None,
    now: datetime | None = None,
) -> str:
    """Map approval + schedule into the labels the Tests UI shows."""
    approval = (approval_status or APPROVAL_DRAFT).upper()
    record = (record_status or "").upper()
    moment = now or datetime.now(timezone.utc)
    start = _as_aware(starts_at)
    end = _as_aware(ends_at)

    if approval == APPROVAL_REJECTED:
        return WORKFLOW_REJECTED
    if approval == APPROVAL_PENDING:
        return WORKFLOW_PENDING
    if approval in {"", APPROVAL_DRAFT} and record in {"", "DRAFT"}:
        return WORKFLOW_DRAFT
    if approval != APPROVAL_APPROVED and record in {"", "DRAFT"}:
        return WORKFLOW_DRAFT

    if record in {"CLOSED", "PUBLISHED", "UNDER_REVIEW", "MARKS_ENTRY"}:
        return WORKFLOW_COMPLETED
    if end is not None and moment >= end:
        return WORKFLOW_COMPLETED
    if record in {"ONGOING"} or (start is not None and moment >= start and (end is None or moment < end)):
        # A scheduled test is live once it has started, even if record status is still SCHEDULED.
        if start is not None and moment >= start and (end is None or moment < end):
            return WORKFLOW_LIVE
        if record in {"ONGOING"}:
            return WORKFLOW_LIVE
    if record in {"CLOSED"}:
        return WORKFLOW_COMPLETED
    return WORKFLOW_SCHEDULED


def ends_from_duration(starts_at: datetime | None, duration_minutes: int | None) -> datetime | None:
    if starts_at is None or not duration_minutes:
        return None
    return _as_aware(starts_at) + timedelta(minutes=int(duration_minutes))
