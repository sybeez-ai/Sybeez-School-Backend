"""Pure rules for audience, opt-in, quiet hours, status, and SMS fallback."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from src.modules.alerts.catalog import PARENT_EVENTS, STAFF_EVENTS, URGENT_EVENTS

FORWARD_STATUS = {
    "PENDING": {"SENT", "FAILED", "SCHEDULED"},
    "SCHEDULED": {"PENDING", "SENT", "FAILED"},
    "SENT": {"DELIVERED", "READ", "FAILED"},
    "DELIVERED": {"READ", "FAILED"},
    "READ": set(),
    "FAILED": set(),
}


def audience_for(event_key: str) -> str:
    if event_key in PARENT_EVENTS:
        return "PARENT"
    if event_key in STAFF_EVENTS:
        return "STAFF"
    return "EXPLICIT"


def recipient_ids(
    event_key: str,
    *,
    parent_ids: list,
    staff_ids: list,
    explicit_ids: list | None = None,
    student_context: bool = False,
) -> list:
    """Parents receive student events. Staff receive payroll and HR events.

    Student leave uses the leave_approved template but resolves parents when a
    student is in context. Announcements use the caller-supplied list.
    """
    if event_key == "announcement":
        return list(explicit_ids or [])
    if event_key in PARENT_EVENTS or (event_key == "leave_approved" and student_context):
        return list(parent_ids)
    if event_key in STAFF_EVENTS:
        return list(staff_ids)
    return list(explicit_ids or [])


def choose_channel(
    *,
    whatsapp_school: bool,
    whatsapp_opt_in: bool,
    whatsapp_opt_out: bool,
    has_phone: bool,
) -> str | None:
    """WhatsApp (Meta Cloud API) is the only outbound channel. It needs the school switch, the
    parent's own opt-in, no STOP opt-out, and a phone number."""
    if has_phone and whatsapp_school and whatsapp_opt_in and not whatsapp_opt_out:
        return "WHATSAPP"
    return None


def is_quiet_hours(now: datetime, start: time, end: time, tz_name: str = "Asia/Kolkata") -> bool:
    local = _as_local(now, tz_name).timetz().replace(tzinfo=None)
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


def next_send_at(now: datetime, start: time, end: time, tz_name: str = "Asia/Kolkata") -> datetime:
    """Next instant the quiet window opens, as an aware UTC datetime."""
    zone = ZoneInfo(tz_name)
    local = _as_local(now, tz_name)
    opening = local.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    if not is_quiet_hours(now, start, end, tz_name):
        return local.astimezone(timezone.utc)
    if start < end:
        if local.timetz().replace(tzinfo=None) >= end:
            opening = opening + timedelta(days=1)
    elif local.timetz().replace(tzinfo=None) >= start:
        opening = opening + timedelta(days=1)
    return opening.replace(tzinfo=zone).astimezone(timezone.utc)


def delivery_timing(
    *,
    now: datetime,
    start: time,
    end: time,
    tz_name: str,
    urgent: bool,
) -> tuple[str, datetime | None]:
    if urgent or not is_quiet_hours(now, start, end, tz_name):
        return "PENDING", None
    return "SCHEDULED", next_send_at(now, start, end, tz_name)


def is_urgent(event_key: str, urgent: bool | None) -> bool:
    if urgent is not None:
        return urgent
    return event_key in URGENT_EVENTS


def next_status(current: str | None, incoming: str) -> str | None:
    current_name = (current or "PENDING").upper()
    incoming_name = (incoming or "").upper()
    if incoming_name == current_name:
        return current_name
    if incoming_name in FORWARD_STATUS.get(current_name, set()):
        return incoming_name
    return None


def service_window_open(last_inbound_at: datetime | None, now: datetime) -> bool:
    if last_inbound_at is None:
        return False
    if last_inbound_at.tzinfo is None:
        last_inbound_at = last_inbound_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return timedelta(0) <= (now - last_inbound_at) <= timedelta(hours=24)


def _as_local(now: datetime, tz_name: str) -> datetime:
    zone = ZoneInfo(tz_name)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(zone)
