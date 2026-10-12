"""Central notification service — used by Attendance, Homework, Exams, Communication."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Notification, NotificationPreference
from src.shared.enums import NotificationChannel, NotificationPriority, NotificationType

# Types that users cannot disable for in-app delivery
NON_DISABLEABLE = {
    NotificationType.SYSTEM.value,
    NotificationType.LEAVE_APPROVED.value,
    NotificationType.LEAVE_REJECTED.value,
    NotificationType.PAYSLIP_PUBLISHED.value,
}


@asynccontextmanager
async def notification_fanout_scope(db: AsyncSession, school_id: UUID | None):
    """Let a staff action (teacher publishes homework, marks attendance) write notification
    rows owned by *other* users of the same school.

    notifications/notification_preferences are user-private under RLS, so a teacher session
    could not insert a student's notification (403 "outside your school, class, or child
    scope"). The role policy is relaxed only for this block and only transaction-locally;
    the school policy's WITH CHECK still pins every written row to the session's school.
    """
    tenant = db.info.get("rls_tenant") if isinstance(db.info.get("rls_tenant"), dict) else None
    session_school = (tenant or {}).get("school_id") or ""
    scoped = (
        school_id is not None
        and tenant is not None
        and tenant.get("bypass") != "on"
        and session_school == str(school_id)
    )
    if not scoped:
        yield
        return
    await db.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    try:
        yield
    finally:
        await db.execute(text("SELECT set_config('app.rls_bypass', 'off', true)"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _in_app_allowed(db: AsyncSession, user_id: UUID, notification_type: str | None) -> bool:
    if not notification_type:
        return True
    if notification_type in NON_DISABLEABLE:
        return True
    pref = (
        await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == notification_type,
            )
        )
    ).scalar_one_or_none()
    if pref is None:
        return True
    return bool(pref.in_app_enabled)


async def create_in_app_notification(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    user_id: UUID,
    title: str,
    body: str,
    category: str | None = None,
    link_path: str | None = None,
    meta: dict | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    priority: str = NotificationPriority.NORMAL.value,
    event_key: str | None = None,
    expires_at: datetime | None = None,
) -> Notification | None:
    """Create an in-app notification (idempotent when event_key is set).

    Respects user preferences. Returns existing row on duplicate event_key.
    """
    async with notification_fanout_scope(db, school_id):
        return await _create_in_app(
            db,
            school_id=school_id,
            user_id=user_id,
            title=title,
            body=body,
            category=category,
            link_path=link_path,
            meta=meta,
            entity_type=entity_type,
            entity_id=entity_id,
            priority=priority,
            event_key=event_key,
            expires_at=expires_at,
        )


async def _create_in_app(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    user_id: UUID,
    title: str,
    body: str,
    category: str | None,
    link_path: str | None,
    meta: dict | None,
    entity_type: str | None,
    entity_id: UUID | None,
    priority: str,
    event_key: str | None,
    expires_at: datetime | None,
) -> Notification | None:
    if not await _in_app_allowed(db, user_id, category):
        return None

    if event_key:
        existing = (
            await db.execute(
                select(Notification).where(
                    Notification.event_key == event_key,
                    Notification.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return existing

    row = Notification(
        school_id=school_id,
        user_id=user_id,
        title=title,
        body=body,
        channel=NotificationChannel.IN_APP.value,
        category=category,
        link_path=link_path,
        meta_json=json.dumps(meta) if meta else None,
        entity_type=entity_type,
        entity_id=entity_id,
        priority=priority,
        event_key=event_key,
        expires_at=expires_at,
    )
    db.add(row)
    await db.flush()
    return row


async def create_bulk_notifications(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    recipient_user_ids: list[UUID],
    title: str,
    body: str,
    category: str | None = None,
    link_path: str | None = None,
    meta: dict | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    priority: str = NotificationPriority.NORMAL.value,
    event_key_prefix: str | None = None,
) -> int:
    """Bulk-create notifications. Returns count of rows inserted/attempted."""
    if not recipient_user_ids:
        return 0
    async with notification_fanout_scope(db, school_id):
        return await _create_bulk(
            db,
            school_id=school_id,
            recipient_user_ids=recipient_user_ids,
            title=title,
            body=body,
            category=category,
            link_path=link_path,
            meta=meta,
            entity_type=entity_type,
            entity_id=entity_id,
            priority=priority,
            event_key_prefix=event_key_prefix,
        )


async def _create_bulk(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    recipient_user_ids: list[UUID],
    title: str,
    body: str,
    category: str | None,
    link_path: str | None,
    meta: dict | None,
    entity_type: str | None,
    entity_id: UUID | None,
    priority: str,
    event_key_prefix: str | None,
) -> int:
    # Deduplicate recipients
    unique_ids = list(dict.fromkeys(recipient_user_ids))
    allowed: list[UUID] = []
    for uid in unique_ids:
        if await _in_app_allowed(db, uid, category):
            allowed.append(uid)
    if not allowed:
        return 0

    meta_s = json.dumps(meta) if meta else None
    rows = []
    for uid in allowed:
        ek = f"{event_key_prefix}:{uid}" if event_key_prefix else None
        rows.append(
            {
                "id": __import__("uuid").uuid4(),
                "school_id": school_id,
                "user_id": uid,
                "title": title,
                "body": body,
                "channel": NotificationChannel.IN_APP.value,
                "category": category,
                "link_path": link_path,
                "meta_json": meta_s,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "priority": priority,
                "event_key": ek,
                "is_read": False,
                "created_at": _utcnow(),
                "updated_at": _utcnow(),
            }
        )

    stmt = pg_insert(Notification).values(rows)
    if event_key_prefix:
        stmt = stmt.on_conflict_do_nothing(index_elements=["event_key", "user_id"])
    await db.execute(stmt)
    await db.flush()
    return len(rows)


# Friendly aliases matching Phase 9 naming
async def notify(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    recipient_user_id: UUID,
    notification_type: NotificationType | str,
    title: str,
    message: str,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    action_url: str | None = None,
    priority: NotificationPriority | str = NotificationPriority.NORMAL,
    event_key: str | None = None,
    meta: dict | None = None,
) -> Notification | None:
    ntype = notification_type.value if isinstance(notification_type, NotificationType) else notification_type
    prio = priority.value if isinstance(priority, NotificationPriority) else priority
    return await create_in_app_notification(
        db,
        school_id=school_id,
        user_id=recipient_user_id,
        title=title,
        body=message,
        category=ntype,
        link_path=action_url,
        meta=meta,
        entity_type=entity_type,
        entity_id=entity_id,
        priority=prio,
        event_key=event_key,
    )
