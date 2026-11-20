from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.jobs import enqueue_job
from src.db.models import NotificationDelivery, NotificationPreference, User
from src.shared.enums import NotificationChannel


async def queue_outbound_delivery(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    user_id: UUID,
    channel: str,
    notification_type: str | None,
    title: str | None,
    body: str,
    link_path: str | None = None,
    meta: dict | None = None,
) -> NotificationDelivery | None:
    """
    Queue an outbound delivery (WHATSAPP only; SMS was dropped) if allowed by preferences and
    the user has a phone number.
    """
    # Preferences gate
    pref = (
        await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == (notification_type or ""),
            )
        )
    ).scalar_one_or_none()

    if channel != NotificationChannel.WHATSAPP.value:
        return None
    if channel == NotificationChannel.WHATSAPP.value:
        # Default is OFF unless explicitly enabled in DB
        if pref is None or not getattr(pref, "whatsapp_enabled", False):
            return None

    user = await db.get(User, user_id)
    if not user or not user.phone:
        return None

    settings = get_settings()
    provider = settings.whatsapp_provider.lower().strip()

    row = NotificationDelivery(
        school_id=school_id,
        user_id=user_id,
        channel=channel,
        notification_type=notification_type,
        to=user.phone,
        title=title,
        body=body,
        meta_json=json.dumps({**(meta or {}), "link_path": link_path} if link_path or meta else None)
        if (link_path or meta)
        else None,
        provider=provider or "mock",
        status="PENDING",
        attempts=0,
    )
    db.add(row)
    await db.flush()

    await enqueue_job(
        "deliveries",
        {
            "delivery_id": str(row.id),
            "provider": row.provider,
            "channel": row.channel,
            "to": row.to,
            "title": row.title,
            "body": row.body,
            "notification_type": row.notification_type,
        },
    )
    return row


async def update_delivery_result(
    db: AsyncSession,
    *,
    delivery_id: UUID,
    status: str,
    provider_message_id: str | None = None,
    last_error: str | None = None,
    attempts: int | None = None,
    error_code: str | None = None,
    scheduled_for: datetime | None = None,
) -> NotificationDelivery:
    from src.modules.alerts.service import apply_worker_result

    return await apply_worker_result(
        db,
        delivery_id=delivery_id,
        status=status,
        provider_message_id=provider_message_id,
        last_error=last_error,
        attempts=attempts,
        error_code=error_code,
        scheduled_for=scheduled_for,
    )

