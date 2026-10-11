"""Tell an employee their payslip is published: in-app, and SMS, WhatsApp, or email when they opted in."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jobs import enqueue_job
from src.db.models import NotificationDelivery, NotificationPreference, User
from src.modules.notifications.deliveries import queue_outbound_delivery
from src.modules.notifications.service import create_in_app_notification
from src.shared.enums import NotificationChannel, NotificationType


async def notify_payslip_published(
    db: AsyncSession,
    *,
    school_id: UUID,
    user_id: UUID | None,
    payslip_id: UUID,
    period_label: str,
    net: str,
) -> None:
    if user_id is None:
        return
    title = "Your payslip is ready"
    body = f"Payslip for {period_label} is published. Net pay INR {net}."
    link = "/hrm/payslips"
    await create_in_app_notification(
        db,
        school_id=school_id,
        user_id=user_id,
        title=title,
        body=body,
        category=NotificationType.PAYSLIP_PUBLISHED.value,
        link_path=link,
        entity_type="payroll_payslip",
        entity_id=payslip_id,
        event_key=f"payslip:{payslip_id}",
    )
    for channel in (NotificationChannel.SMS.value, NotificationChannel.WHATSAPP.value):
        try:
            await queue_outbound_delivery(
                db,
                school_id=school_id,
                user_id=user_id,
                channel=channel,
                notification_type=NotificationType.PAYSLIP_PUBLISHED.value,
                title=title,
                body=body,
                link_path=link,
                meta={"payslip_id": str(payslip_id)},
            )
        except Exception:
            from src.core.telemetry import track_alert

            track_alert("payroll_notify_failed", channel=channel, payslip_id=str(payslip_id))
    pref = (
        await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == NotificationType.PAYSLIP_PUBLISHED.value,
            )
        )
    ).scalar_one_or_none()
    if pref is None or not pref.email_enabled:
        return
    user = await db.get(User, user_id)
    if user is None or not user.email:
        return
    row = NotificationDelivery(
        school_id=school_id,
        user_id=user_id,
        channel=NotificationChannel.EMAIL.value,
        notification_type=NotificationType.PAYSLIP_PUBLISHED.value,
        to=user.email,
        title=title,
        body=body,
        meta_json=json.dumps({"payslip_id": str(payslip_id), "link_path": link}),
        provider="mock",
        status="PENDING",
        attempts=0,
    )
    db.add(row)
    await db.flush()
    try:
        await enqueue_job(
            "deliveries",
            {
                "delivery_id": str(row.id),
                "provider": "mock",
                "channel": NotificationChannel.EMAIL.value,
                "to": user.email,
                "title": title,
                "body": body,
                "notification_type": NotificationType.PAYSLIP_PUBLISHED.value,
            },
        )
    except Exception:
        from src.core.telemetry import track_alert

        track_alert("payroll_notify_failed", channel="EMAIL", payslip_id=str(payslip_id))
