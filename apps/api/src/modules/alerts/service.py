"""Queue WhatsApp template alerts (per-parent language) and apply Meta delivery status."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jobs import enqueue_job
from src.db.models import (
    AlertAuditLog,
    AlertChannelOptOut,
    AlertInboundMessage,
    AlertMessageTemplate,
    NotificationDelivery,
    NotificationPreference,
    Parent,
    ParentMessagePreference,
    School,
    SchoolAlertSettings,
    StudentParent,
    User,
)
from src.modules.alerts.catalog import EVENT_NOTIFICATION_TYPE, ordered_params, render_message, template_components
from src.modules.alerts.phone import mask_phone, mask_text, normalize_phone, phone_variants
from src.modules.alerts.policy import (
    choose_channel,
    delivery_timing,
    is_urgent,
    next_status,
    recipient_ids,
    service_window_open,
)
from src.modules.alerts.webhooks import InboundUpdate, StatusUpdate
from src.shared.enums import NotificationChannel

logger = logging.getLogger("sybeez.alerts")

PAYROLL_STAFF_ROLES = (
    "SCHOOL_ADMIN",
    "HR_MANAGER",
    "PAYROLL_ADMIN",
    "PAYROLL_APPROVER",
    "ACCOUNTANT",
)


@dataclass
class DispatchResult:
    queued: int = 0
    scheduled: int = 0
    skipped: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def dispatch_alert(
    db: AsyncSession,
    *,
    school_id: UUID,
    event_key: str,
    variables: dict,
    notification_type: str | None = None,
    student_id: UUID | None = None,
    staff_user_ids: list[UUID] | None = None,
    explicit_user_ids: list[UUID] | None = None,
    urgent: bool | None = None,
    dedupe_key: str | None = None,
    title: str | None = None,
    link_path: str | None = None,
    locale: str | None = None,
    localized_variables: dict[str, dict] | None = None,
    now: datetime | None = None,
) -> DispatchResult:
    await _scope_school(db, school_id)
    settings = await _settings(db, school_id)
    result = DispatchResult()
    if settings is None or not settings.whatsapp_enabled:
        result.skipped += 1
        return result

    notification_type = notification_type or EVENT_NOTIFICATION_TYPE.get(event_key)
    parent_ids = await _parent_user_ids(db, school_id, student_id) if student_id else []
    ids = recipient_ids(
        event_key,
        parent_ids=parent_ids,
        staff_ids=list(staff_user_ids or []),
        explicit_ids=list(explicit_user_ids or []),
        student_context=student_id is not None,
    )
    if not ids:
        return result

    users = list((await db.execute(select(User).where(User.id.in_(ids)))).scalars().all())
    by_id = {user.id: user for user in users}
    moment = now or _utcnow()
    urgent_send = is_urgent(event_key, urgent)
    default_locale = locale or settings.default_locale or "en"
    base_values = {key: "" if value is None else str(value) for key, value in variables.items()}
    parent_prefs = {
        pref.user_id: pref
        for pref in (
            await db.execute(
                select(ParentMessagePreference).where(
                    ParentMessagePreference.school_id == school_id, ParentMessagePreference.user_id.in_(ids)
                )
            )
        ).scalars()
    }

    for user_id in ids:
        user = by_id.get(user_id)
        if user is None:
            result.skipped += 1
            continue
        phone = await _phone_for(db, school_id, user)
        mpref = parent_prefs.get(user.id)
        if mpref is not None:
            # The parent's own consent record wins: explicit, timestamped, withdrawable.
            opt_in = bool(mpref.whatsapp_opt_in)
        else:
            legacy = await _preference(db, user.id, notification_type or "")
            opt_in = bool(legacy and legacy.whatsapp_enabled)
        whatsapp_out = await _opted_out(db, school_id, phone, NotificationChannel.WHATSAPP.value)
        channel = choose_channel(
            whatsapp_school=settings.whatsapp_enabled,
            whatsapp_opt_in=opt_in,
            whatsapp_opt_out=whatsapp_out,
            has_phone=bool(phone),
        )
        if channel is None or not phone:
            result.skipped += 1
            continue
        key = f"{dedupe_key}:{user.id}:{channel}" if dedupe_key else None
        if key and await _duplicate(db, school_id, channel, key):
            result.skipped += 1
            continue
        status, scheduled_for = delivery_timing(
            now=moment,
            start=_as_time(settings.quiet_hours_start, time(21, 0)),
            end=_as_time(settings.quiet_hours_end, time(7, 0)),
            tz_name=settings.timezone or "Asia/Kolkata",
            urgent=urgent_send,
        )
        # The alert goes out in the parent's chosen language (their own translated variables when the
        # caller supplied per-language content), falling back to the school default, then English.
        user_locale = (mpref.preferred_language if mpref is not None else None) or default_locale
        template = await _template(db, school_id, event_key, user_locale)
        if template is None:
            await _audit(db, school_id, None, "alert.template_missing", "alert_template", None, {"event_key": event_key, "locale": user_locale})
            result.skipped += 1
            continue
        values = dict(base_values)
        values.update((localized_variables or {}).get(template.locale, {}))
        body = render_message(template.body_text, list(template.variables or []), values)
        components = template_components(list(template.variables or []), values)
        params = ordered_params(list(template.variables or []), values)
        window = await _window_open(db, phone, moment)
        # Alerts are business-initiated, so WhatsApp always uses the approved template.
        # Free text is sent only for a session reply inside the 24-hour window.
        message_mode = "template"
        rendered = body
        row = NotificationDelivery(
            school_id=school_id,
            user_id=user.id,
            channel=channel,
            notification_type=notification_type,
            to=normalize_phone(phone),
            title=title,
            body=rendered,
            meta_json=json.dumps(
                {
                    "event_key": event_key,
                    "link_path": link_path,
                    "components": components,
                    "service_window_open": window,
                }
            ),
            provider=_provider_for(channel),
            status=status,
            attempts=0,
            event_key=event_key,
            locale=template.locale,
            template_name=template.whatsapp_template_name,
            language_code=template.language_code,
            variables_json=json.dumps(values),
            scheduled_for=scheduled_for,
            dedupe_key=key,
            message_mode=message_mode,
        )
        db.add(row)
        await db.flush()
        if status == "PENDING":
            await _enqueue_delivery(row, components, window)
            result.queued += 1
            action = "alert.queued"
        else:
            result.scheduled += 1
            action = "alert.scheduled"
        await _audit(
            db,
            school_id,
            None,
            action,
            "notification_delivery",
            row.id,
            {"event_key": event_key, "channel": channel, "to": mask_phone(phone), "status": status},
        )
    return result


async def enable_rls_bypass(db: AsyncSession) -> None:
    """Webhook and scheduler requests have no school JWT. New alert tables are forced RLS."""
    db.info["rls_tenant"] = {"school_id": "", "user_id": "", "role": "SERVICE", "bypass": "on"}
    await db.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    await db.execute(text("SELECT set_config('app.role', 'SERVICE', true)"))


async def release_due_deliveries(db: AsyncSession, *, now: datetime | None = None, limit: int = 200) -> dict:
    await enable_rls_bypass(db)
    moment = now or _utcnow()
    if moment.tzinfo is None:
        # Callers (cron, tests) may pass a naive UTC time; scheduled_for is TIMESTAMPTZ.
        moment = moment.replace(tzinfo=timezone.utc)
    rows = list(
        (
            await db.execute(
                select(NotificationDelivery)
                .where(
                    NotificationDelivery.status == "SCHEDULED",
                    NotificationDelivery.scheduled_for.is_not(None),
                    NotificationDelivery.scheduled_for <= moment,
                )
                .order_by(NotificationDelivery.scheduled_for.asc())
                .limit(limit)
            )
        ).scalars().all()
    )
    released = 0
    for row in rows:
        row.status = "PENDING"
        meta = _meta(row)
        await _enqueue_delivery(
            row,
            meta.get("components") or [],
            bool(meta.get("service_window_open")),
        )
        released += 1
    if released:
        await _audit(db, None, None, "alert.released", "notification_delivery", None, {"count": released})
    return {"released": released}


async def apply_provider_status(
    db: AsyncSession,
    update: StatusUpdate,
    *,
    channel: str | None = None,
) -> NotificationDelivery | None:
    query = select(NotificationDelivery).where(NotificationDelivery.provider_message_id == update.provider_message_id)
    if channel:
        query = query.where(NotificationDelivery.channel == channel)
    row = (await db.execute(query.order_by(NotificationDelivery.created_at.desc()))).scalars().first()
    if row is None:
        return None
    return await _transition(db, row, update.status, error_code=update.error_code, error_message=update.error_message)


async def store_inbound(db: AsyncSession, item: InboundUpdate, *, channel: str, raw: str | None = None) -> AlertInboundMessage:
    school_id, user_ids = await _match_phone(db, item.from_phone)
    row = AlertInboundMessage(
        school_id=school_id,
        channel=channel,
        from_phone=item.from_phone,
        provider_message_id=item.provider_message_id,
        body=item.body,
        raw_json=raw,
        opt_out=item.opt_out,
        received_at=_utcnow(),
    )
    db.add(row)
    await db.flush()
    if item.opt_out:
        await _record_opt_out(db, school_id=school_id, user_ids=user_ids, phone=item.from_phone, channel=channel)
    await _audit(
        db,
        school_id,
        None,
        "alert.opt_out" if item.opt_out else "alert.inbound",
        "alert_inbound_message",
        row.id,
        {"channel": channel, "from": mask_phone(item.from_phone), "opt_out": item.opt_out},
    )
    return row


async def resend_delivery(db: AsyncSession, row: NotificationDelivery) -> NotificationDelivery:
    if row.status != "FAILED":
        from src.core.exceptions import ConflictError

        raise ConflictError("Only a failed delivery can be resent")
    row.status = "PENDING"
    row.last_error = None
    row.error_code = None
    row.failed_at = None
    meta = _meta(row)
    await db.flush()
    await _enqueue_delivery(
        row,
        meta.get("components") or [],
        bool(meta.get("service_window_open")),
    )
    await _audit(
        db,
        row.school_id,
        None,
        "alert.resend",
        "notification_delivery",
        row.id,
        {"channel": row.channel, "to": mask_phone(row.to)},
    )
    return row


async def record_audit(
    db: AsyncSession,
    school_id: UUID | None,
    actor_user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    meta: dict,
) -> None:
    safe = dict(meta)
    if "phone" in safe:
        safe["phone"] = mask_phone(str(safe["phone"]))
    if "to" in safe:
        safe["to"] = mask_phone(str(safe["to"]))
    await _audit(db, school_id, actor_user_id, action, entity_type, entity_id, safe)


async def _transition(
    db: AsyncSession,
    row: NotificationDelivery,
    status: str,
    *,
    error_code: str | None,
    error_message: str | None,
    attempts: int | None = None,
    provider_message_id: str | None = None,
    scheduled_for: datetime | None = None,
) -> NotificationDelivery:
    nxt = next_status(row.status, status)
    if nxt is None:
        return row
    row.status = nxt
    if provider_message_id:
        row.provider_message_id = provider_message_id
    if error_code:
        row.error_code = error_code[:64]
    if error_message:
        row.last_error = mask_text(error_message)[:2000]
    if attempts is not None:
        row.attempts = attempts
    if scheduled_for is not None:
        row.scheduled_for = scheduled_for
    moment = _utcnow()
    if nxt == "SENT" and row.sent_at is None:
        row.sent_at = moment
    elif nxt == "DELIVERED":
        row.delivered_at = row.delivered_at or moment
    elif nxt == "READ":
        row.read_at = row.read_at or moment
        row.delivered_at = row.delivered_at or moment
    elif nxt == "FAILED":
        row.failed_at = moment
    return row


async def apply_worker_result(
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
    from src.core.exceptions import NotFoundError

    row = await db.get(NotificationDelivery, delivery_id)
    if row is None:
        raise NotFoundError("Delivery not found")
    if row.school_id is not None:
        await _scope_school(db, row.school_id)
    return await _transition(
        db,
        row,
        status,
        error_code=error_code,
        error_message=last_error,
        attempts=attempts,
        provider_message_id=provider_message_id,
        scheduled_for=scheduled_for,
    )


async def _record_opt_out(
    db: AsyncSession,
    *,
    school_id: UUID | None,
    user_ids: list[UUID],
    phone: str,
    channel: str,
) -> None:
    existing = (
        await db.execute(
            select(AlertChannelOptOut).where(
                AlertChannelOptOut.phone == phone,
                AlertChannelOptOut.channel == channel,
                AlertChannelOptOut.school_id == school_id if school_id else AlertChannelOptOut.school_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            AlertChannelOptOut(
                school_id=school_id,
                user_id=user_ids[0] if user_ids else None,
                phone=phone,
                channel=channel,
                source="STOP",
                opted_out_at=_utcnow(),
            )
        )
    if user_ids:
        prefs = list(
            (
                await db.execute(select(NotificationPreference).where(NotificationPreference.user_id.in_(user_ids)))
            ).scalars().all()
        )
        for pref in prefs:
            if channel == NotificationChannel.WHATSAPP.value:
                pref.whatsapp_enabled = False
    await db.flush()


async def _match_phone(db: AsyncSession, phone: str) -> tuple[UUID | None, list[UUID]]:
    variants = phone_variants(phone)
    users = list((await db.execute(select(User).where(User.phone.in_(variants)))).scalars().all()) if variants else []
    user_ids = [user.id for user in users]
    delivery = (
        await db.execute(
            select(NotificationDelivery)
            .where(NotificationDelivery.to.in_(variants or [phone]))
            .order_by(NotificationDelivery.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    school_id = delivery.school_id if delivery and delivery.school_id else None
    if school_id is None and user_ids:
        parent = (
            await db.execute(select(Parent).where(Parent.user_id.in_(user_ids)).limit(1))
        ).scalar_one_or_none()
        if parent:
            school_id = parent.school_id
    if delivery and delivery.user_id and delivery.user_id not in user_ids:
        user_ids.append(delivery.user_id)
    return school_id, user_ids


async def _parent_user_ids(db: AsyncSession, school_id: UUID, student_id: UUID) -> list[UUID]:
    rows = (
        await db.execute(
            select(Parent.user_id)
            .join(StudentParent, StudentParent.parent_id == Parent.id)
            .where(
                StudentParent.school_id == school_id,
                StudentParent.student_id == student_id,
                Parent.school_id == school_id,
            )
        )
    ).scalars().all()
    return list(dict.fromkeys(rows))


async def _settings(db: AsyncSession, school_id: UUID) -> SchoolAlertSettings | None:
    return await db.get(SchoolAlertSettings, school_id)


async def _template(db: AsyncSession, school_id: UUID, event_key: str, locale: str) -> AlertMessageTemplate | None:
    school_row = (
        await db.execute(
            select(AlertMessageTemplate).where(
                AlertMessageTemplate.school_id == school_id,
                AlertMessageTemplate.event_key == event_key,
                AlertMessageTemplate.locale == locale,
            )
        )
    ).scalar_one_or_none()
    if school_row is not None:
        return school_row if school_row.active else None
    platform = (
        await db.execute(
            select(AlertMessageTemplate).where(
                AlertMessageTemplate.school_id.is_(None),
                AlertMessageTemplate.event_key == event_key,
                AlertMessageTemplate.locale == locale,
                AlertMessageTemplate.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if platform is not None:
        return platform
    if locale != "en":
        return await _template(db, school_id, event_key, "en")
    return None


async def _preference(db: AsyncSession, user_id: UUID, notification_type: str) -> NotificationPreference | None:
    return (
        await db.execute(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user_id,
                NotificationPreference.notification_type == notification_type,
            )
        )
    ).scalar_one_or_none()


async def _opted_out(db: AsyncSession, school_id: UUID, phone: str | None, channel: str) -> bool:
    if not phone:
        return False
    norm = normalize_phone(phone)
    row = (
        await db.execute(
            select(AlertChannelOptOut.id).where(
                AlertChannelOptOut.channel == channel,
                AlertChannelOptOut.phone == norm,
                or_(AlertChannelOptOut.school_id == school_id, AlertChannelOptOut.school_id.is_(None)),
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def _phone_for(db: AsyncSession, school_id: UUID, user: User) -> str | None:
    if user.phone:
        return user.phone
    parent = (
        await db.execute(
            select(Parent).where(Parent.user_id == user.id, Parent.school_id == school_id).limit(1)
        )
    ).scalar_one_or_none()
    return parent.phone if parent and parent.phone else None


async def _duplicate(db: AsyncSession, school_id: UUID, channel: str, dedupe_key: str) -> bool:
    row = (
        await db.execute(
            select(NotificationDelivery.id).where(
                NotificationDelivery.school_id == school_id,
                NotificationDelivery.channel == channel,
                NotificationDelivery.dedupe_key == dedupe_key,
                NotificationDelivery.status != "FAILED",
            ).limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def _window_open(db: AsyncSession, phone: str, now: datetime) -> bool:
    last = (
        await db.execute(
            select(AlertInboundMessage.received_at)
            .where(AlertInboundMessage.from_phone == normalize_phone(phone))
            .order_by(AlertInboundMessage.received_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return service_window_open(last, now)


async def _enqueue_delivery(row: NotificationDelivery, components: list, window: bool) -> None:
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
            "message_mode": row.message_mode,
            "template_name": row.template_name,
            "language_code": row.language_code,
            "components": components,
            "service_window_open": window,
            "event_key": row.event_key,
        },
    )


def _provider_for(channel: str) -> str:
    from src.core.config import get_settings
    from src.modules.alerts.credentials import effective_whatsapp_provider

    return effective_whatsapp_provider(get_settings())


async def _scope_school(db: AsyncSession, school_id: UUID) -> None:
    await db.execute(text("SELECT set_config('app.school_id', :v, true)"), {"v": str(school_id)})


async def _audit(
    db: AsyncSession,
    school_id: UUID | None,
    actor_user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None,
    meta: dict,
) -> None:
    db.add(
        AlertAuditLog(
            school_id=school_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            meta_json=json.dumps(meta, default=str),
        )
    )
    await db.flush()
    logger.info("alert_audit action=%s entity=%s meta=%s", action, entity_type, meta)


def _meta(row: NotificationDelivery) -> dict:
    if not row.meta_json:
        return {}
    try:
        data = json.loads(row.meta_json)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _as_time(value: time | None, default: time) -> time:
    return value or default


async def school_display_name(db: AsyncSession, school_id: UUID) -> str:
    school = await db.get(School, school_id)
    return school.name if school else "School"
