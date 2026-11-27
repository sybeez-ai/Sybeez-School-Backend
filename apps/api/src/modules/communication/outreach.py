"""Send a school message over the channels an admin selected.

If a channel has no live provider, the delivery is stored as failed with
"not sent: provider not configured". Success is never faked via the mock provider.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.jobs import enqueue_job
from src.db.models import NotificationDelivery, Parent, Student, Teacher, User
from src.modules.notifications.service import create_in_app_notification, notification_fanout_scope
from src.shared.enums import NotificationChannel

NOT_CONFIGURED = "not sent: provider not configured"


def channel_provider_status(channel: str) -> tuple[bool, str]:
    """Return (ready, provider_name) for a delivery channel."""
    channel = (channel or "").upper()
    settings = get_settings()
    if channel == NotificationChannel.IN_APP.value:
        return True, "in_app"
    if channel == NotificationChannel.WHATSAPP.value:
        from src.modules.alerts.credentials import (
            effective_whatsapp_provider,
            whatsapp_send_ready,
            whatsapp_sending_on,
        )

        if whatsapp_sending_on(settings) and whatsapp_send_ready(settings):
            provider = effective_whatsapp_provider(settings)
            if provider and provider != "mock":
                return True, provider
        return False, "none"
    # Email and SMS have no live provider wired in this service.
    return False, "none"


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def _phone_for(db: AsyncSession, school_id: UUID, user: User) -> str | None:
    if user.phone:
        return user.phone
    teacher = (
        await db.execute(select(Teacher).where(Teacher.school_id == school_id, Teacher.user_id == user.id))
    ).scalar_one_or_none()
    if teacher and getattr(teacher, "phone", None):
        return teacher.phone
    parent = (
        await db.execute(select(Parent).where(Parent.school_id == school_id, Parent.user_id == user.id))
    ).scalar_one_or_none()
    if parent and getattr(parent, "phone", None):
        return parent.phone
    student = (
        await db.execute(select(Student).where(Student.school_id == school_id, Student.user_id == user.id))
    ).scalar_one_or_none()
    if student and getattr(student, "phone", None):
        return student.phone
    return None


def _display_status(row: NotificationDelivery) -> str:
    if row.status in {"FAILED", "REJECTED"} and row.last_error:
        return row.last_error
    return row.status


async def dispatch_outreach(
    db: AsyncSession,
    *,
    school_id: UUID,
    recipient_user_ids: list[UUID],
    channels: list[str],
    title: str,
    body: str,
    notification_type: str,
    entity_type: str,
    entity_id: UUID,
    link_path: str | None = None,
    category: str | None = None,
    priority: str = "NORMAL",
) -> list[NotificationDelivery]:
    """Create one delivery row per recipient per channel and queue only live providers."""
    wanted = []
    seen: set[str] = set()
    for raw in channels or []:
        ch = str(raw).upper()
        if ch in seen:
            continue
        seen.add(ch)
        wanted.append(ch)
    if not wanted:
        wanted = [NotificationChannel.IN_APP.value]

    users = list((await db.execute(select(User).where(User.id.in_(recipient_user_ids or [])))).scalars().all())
    by_id = {u.id: u for u in users}
    rows: list[NotificationDelivery] = []

    async with notification_fanout_scope(db, school_id):
        await _write_outreach_rows(
            db,
            school_id=school_id,
            recipient_user_ids=recipient_user_ids or [],
            by_id=by_id,
            wanted=wanted,
            title=title,
            body=body,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
            link_path=link_path,
            category=category,
            priority=priority,
            rows=rows,
        )
    return rows


async def _write_outreach_rows(
    db: AsyncSession,
    *,
    school_id: UUID,
    recipient_user_ids: list,
    by_id: dict,
    wanted: list[str],
    title: str,
    body: str,
    notification_type: str,
    entity_type: str,
    entity_id: UUID,
    link_path: str | None,
    category: str | None,
    priority: str,
    rows: list,
) -> None:
    for user_id in recipient_user_ids:
        user = by_id.get(user_id)
        if user is None:
            continue
        phone = await _phone_for(db, school_id, user)
        for channel in wanted:
            if channel == NotificationChannel.IN_APP.value:
                await create_in_app_notification(
                    db,
                    school_id=school_id,
                    user_id=user_id,
                    title=title,
                    body=body[:2000],
                    category=category or notification_type,
                    link_path=link_path,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    priority=priority,
                    event_key=f"{entity_type}:{entity_id}:{user_id}",
                )
                row = NotificationDelivery(
                    school_id=school_id,
                    user_id=user_id,
                    channel=channel,
                    notification_type=notification_type,
                    to=str(user_id),
                    title=title,
                    body=body,
                    meta_json=json.dumps({"entity_type": entity_type, "entity_id": str(entity_id), "link_path": link_path}),
                    provider="in_app",
                    status="SENT",
                    attempts=1,
                    sent_at=_utcnow(),
                )
                db.add(row)
                await db.flush()
                rows.append(row)
                continue

            ready, provider = channel_provider_status(channel)
            dest = ""
            if channel == NotificationChannel.EMAIL.value:
                dest = (user.email or "").strip()
            elif channel in {NotificationChannel.WHATSAPP.value, NotificationChannel.SMS.value}:
                dest = (phone or "").strip()

            if not ready:
                row = NotificationDelivery(
                    school_id=school_id,
                    user_id=user_id,
                    channel=channel,
                    notification_type=notification_type,
                    to=dest or "n/a",
                    title=title,
                    body=body,
                    meta_json=json.dumps({"entity_type": entity_type, "entity_id": str(entity_id)}),
                    provider=provider or "none",
                    status="FAILED",
                    attempts=0,
                    last_error=NOT_CONFIGURED,
                    error_code="provider_not_configured",
                    failed_at=_utcnow(),
                )
                db.add(row)
                await db.flush()
                rows.append(row)
                continue

            if not dest:
                missing = "not sent: no email address" if channel == NotificationChannel.EMAIL.value else "not sent: no phone number"
                row = NotificationDelivery(
                    school_id=school_id,
                    user_id=user_id,
                    channel=channel,
                    notification_type=notification_type,
                    to="n/a",
                    title=title,
                    body=body,
                    meta_json=json.dumps({"entity_type": entity_type, "entity_id": str(entity_id)}),
                    provider=provider,
                    status="FAILED",
                    attempts=0,
                    last_error=missing,
                    error_code="missing_address",
                    failed_at=_utcnow(),
                )
                db.add(row)
                await db.flush()
                rows.append(row)
                continue

            row = NotificationDelivery(
                school_id=school_id,
                user_id=user_id,
                channel=channel,
                notification_type=notification_type,
                to=dest,
                title=title,
                body=body,
                meta_json=json.dumps(
                    {
                        "entity_type": entity_type,
                        "entity_id": str(entity_id),
                        "link_path": link_path,
                    }
                ),
                provider=provider,
                status="PENDING",
                attempts=0,
                message_mode="text",
            )
            db.add(row)
            await db.flush()
            try:
                await enqueue_job(
                    "deliveries",
                    {
                        "delivery_id": str(row.id),
                        "provider": provider,
                        "channel": channel,
                        "to": dest,
                        "title": title,
                        "body": body,
                        "notification_type": notification_type,
                        "message_mode": "text",
                    },
                )
            except Exception:
                row.status = "FAILED"
                row.last_error = NOT_CONFIGURED
                row.error_code = "enqueue_failed"
                row.failed_at = _utcnow()
            rows.append(row)



def delivery_public_status(row: NotificationDelivery) -> str:
    return _display_status(row)
