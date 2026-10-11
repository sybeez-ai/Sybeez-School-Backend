"""Admin delivery log and provider webhooks.

DeliveryLogView in the frontend can call the /alerts/deliveries endpoints.
Phone numbers in API logs are masked. The admin payload includes both `to`
and `to_masked`; render `to_masked` in the log.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.modules.access.deps import public
from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from src.db.models import AlertMessageTemplate, NotificationDelivery, SchoolAlertSettings
from src.modules.alerts.phone import mask_phone
from src.modules.alerts.credentials import provider_status
from src.modules.alerts.schemas import (
    AlertSettingsOut,
    AlertSettingsUpdate,
    DeliveryListOut,
    DeliveryOut,
    ProviderStatusOut,
    TemplateCreate,
    TemplateOut,
    TemplateSubmitOut,
    TemplateUpdate,
)
from src.modules.alerts.submit import default_post_json, submit_message_templates, template_payload
from src.modules.alerts.service import enable_rls_bypass, record_audit, resend_delivery
from src.modules.alerts.webhooks import (
    loads_json,
    parse_meta_webhook,
    parse_msg91_webhook,
    verify_challenge,
    verify_meta_signature,
    verify_msg91_token,
)
from src.shared.enums import UserRole

webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])
admin_router = APIRouter(prefix="/alerts", tags=["alerts"])

Db = Annotated[AsyncSession, Depends(get_db)]
Admin = Annotated[
    CurrentUser,
    Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN)),
]


def _school_id(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("Pass X-School-Id for this school")
    return user.school_id


def _hhmm(value: time) -> str:
    return f"{value.hour:02d}:{value.minute:02d}"


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _delivery_out(row: NotificationDelivery) -> DeliveryOut:
    payload = DeliveryOut.model_validate(row)
    payload.to_masked = mask_phone(row.to)
    return payload


async def _settings_row(db: AsyncSession, school_id: UUID) -> SchoolAlertSettings:
    row = await db.get(SchoolAlertSettings, school_id)
    if row is None:
        row = SchoolAlertSettings(
            school_id=school_id,
            whatsapp_enabled=False,
            sms_enabled=False,
            quiet_hours_start=time(21, 0),
            quiet_hours_end=time(7, 0),
            timezone="Asia/Kolkata",
            default_locale="en",
        )
        db.add(row)
        await db.flush()
    return row


def _settings_out(row: SchoolAlertSettings) -> AlertSettingsOut:
    return AlertSettingsOut(
        school_id=row.school_id,
        whatsapp_enabled=row.whatsapp_enabled,
        sms_enabled=row.sms_enabled,
        quiet_hours_start=_hhmm(row.quiet_hours_start),
        quiet_hours_end=_hhmm(row.quiet_hours_end),
        timezone=row.timezone,
        default_locale=row.default_locale,
        **provider_status(get_settings()),
    )


def _template_out(row: AlertMessageTemplate, *, source: str) -> TemplateOut:
    return TemplateOut(
        id=row.id,
        school_id=row.school_id,
        event_key=row.event_key,
        locale=row.locale,
        whatsapp_template_name=row.whatsapp_template_name,
        language_code=row.language_code,
        variables=list(row.variables or []),
        sms_dlt_template_id=row.sms_dlt_template_id,
        sms_text=row.sms_text,
        body_text=row.body_text,
        category=row.category,
        active=row.active,
        source=source,
    )


@public
@webhook_router.get("/whatsapp/meta")
async def meta_verify(
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
):
    settings = get_settings()
    if not verify_challenge(hub_mode, hub_verify_token, settings.whatsapp_verify_token):
        raise ForbiddenError("WhatsApp verify token rejected")
    return PlainTextResponse(hub_challenge or "")


@public
@webhook_router.post("/whatsapp/meta")
async def meta_webhook(request: Request, db: Db):
    raw = await request.body()
    settings = get_settings()
    signature = request.headers.get("x-hub-signature-256")
    if not verify_meta_signature(raw, signature, settings.whatsapp_app_secret):
        raise UnauthorizedError("Invalid WhatsApp signature")
    await enable_rls_bypass(db)
    payload = loads_json(raw)
    from src.modules.alerts.service import apply_provider_status, store_inbound

    statuses, inbound = parse_meta_webhook(payload)
    for item in statuses:
        await apply_provider_status(db, item, channel="WHATSAPP")
    raw_text = raw.decode("utf-8", errors="replace")
    for item in inbound:
        await store_inbound(db, item, channel="WHATSAPP", raw=raw_text)
    return {"ok": True}


@public
@webhook_router.post("/sms/msg91")
async def msg91_webhook(
    request: Request,
    db: Db,
    x_msg91_token: Annotated[str | None, Header(alias="X-Msg91-Token")] = None,
):
    settings = get_settings()
    if settings.app_env.lower() == "production" and not settings.msg91_webhook_token:
        raise ForbiddenError("MSG91_WEBHOOK_TOKEN is required in production")
    if settings.msg91_webhook_token and not verify_msg91_token(x_msg91_token, settings.msg91_webhook_token):
        raise UnauthorizedError("Invalid MSG91 webhook token")
    raw = await request.body()
    await enable_rls_bypass(db)
    payload = loads_json(raw)
    from src.modules.alerts.service import apply_provider_status

    for item in parse_msg91_webhook(payload):
        await apply_provider_status(db, item, channel="SMS")
    return {"ok": True}


@admin_router.get("/deliveries", response_model=DeliveryListOut)
async def list_deliveries(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
    channel: str | None = None,
    status: str | None = None,
    event: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    recipient: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    school_id = _school_id(user)
    filters = [NotificationDelivery.school_id == school_id]
    if channel:
        filters.append(NotificationDelivery.channel == channel.upper())
    if status:
        filters.append(NotificationDelivery.status == status.upper())
    if event:
        filters.append(
            (NotificationDelivery.event_key == event) | (NotificationDelivery.notification_type == event)
        )
    if date_from:
        filters.append(NotificationDelivery.created_at >= datetime.combine(date_from, time.min, tzinfo=timezone.utc))
    if date_to:
        filters.append(
            NotificationDelivery.created_at < datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
        )
    if recipient:
        try:
            recipient_id = UUID(recipient)
        except ValueError:
            recipient_id = None
        if recipient_id:
            filters.append(NotificationDelivery.user_id == recipient_id)
        else:
            filters.append(NotificationDelivery.to.ilike(f"%{recipient}%"))
    total = int((await db.execute(select(func.count()).select_from(NotificationDelivery).where(*filters))).scalar_one())
    rows = list(
        (
            await db.execute(
                select(NotificationDelivery)
                .where(*filters)
                .order_by(NotificationDelivery.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
    )
    return DeliveryListOut(items=[_delivery_out(row) for row in rows], total=total)


@admin_router.get("/deliveries/{delivery_id}", response_model=DeliveryOut)
async def get_delivery(
    delivery_id: UUID,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    row = await _owned_delivery(db, _school_id(user), delivery_id)
    return _delivery_out(row)


@admin_router.post("/deliveries/{delivery_id}/resend", response_model=DeliveryOut)
async def resend(
    delivery_id: UUID,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    school_id = _school_id(user)
    row = await _owned_delivery(db, school_id, delivery_id)
    updated = await resend_delivery(db, row)
    await record_audit(
        db,
        school_id,
        user.id,
        "alert.resend_requested",
        "notification_delivery",
        updated.id,
        {"actor": str(user.id)},
    )
    return _delivery_out(updated)


@admin_router.get("/status", response_model=ProviderStatusOut)
async def alert_provider_status(user: Admin):
    """Credential banner for DeliveryLogView. Missing secrets stay mock and do not raise."""
    _ = user
    return ProviderStatusOut(**provider_status(get_settings()))


@admin_router.get("/settings", response_model=AlertSettingsOut)
async def get_settings_route(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    return _settings_out(await _settings_row(db, school_id))


@admin_router.put("/settings", response_model=AlertSettingsOut)
async def update_settings(
    body: AlertSettingsUpdate,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    row = await _settings_row(db, school_id)
    data = body.model_dump(exclude_unset=True)
    if "quiet_hours_start" in data:
        row.quiet_hours_start = _parse_hhmm(data.pop("quiet_hours_start"))
    if "quiet_hours_end" in data:
        row.quiet_hours_end = _parse_hhmm(data.pop("quiet_hours_end"))
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    await record_audit(db, school_id, user.id, "alert.settings_updated", "school_alert_settings", school_id, data)
    return _settings_out(row)


@admin_router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
    event_key: str | None = None,
    locale: str | None = None,
):
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    school_rows = list(
        (
            await db.execute(
                select(AlertMessageTemplate).where(AlertMessageTemplate.school_id == school_id)
            )
        ).scalars().all()
    )
    defaults = list(
        (
            await db.execute(
                select(AlertMessageTemplate).where(AlertMessageTemplate.school_id.is_(None))
            )
        ).scalars().all()
    )
    overridden = {(row.event_key, row.locale) for row in school_rows}
    items = [_template_out(row, source="school") for row in school_rows]
    items.extend(
        _template_out(row, source="default")
        for row in defaults
        if (row.event_key, row.locale) not in overridden
    )
    if event_key:
        items = [item for item in items if item.event_key == event_key]
    if locale:
        items = [item for item in items if item.locale == locale]
    items.sort(key=lambda item: (item.event_key, item.locale))
    return items


@admin_router.post("/templates/submit", response_model=TemplateSubmitOut)
async def submit_templates(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
    event_key: str | None = None,
    locale: str | None = None,
    dry_run: bool = False,
):
    """Post active templates to the WABA message_templates API.

    With no token or business account id the response status is
    "WhatsApp not configured" and Meta is not called.
    """
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    items = await list_templates(db, user, _scope, event_key=event_key, locale=locale)
    payloads = [
        template_payload(
            name=item.whatsapp_template_name,
            language=item.language_code,
            body_text=item.body_text,
            variables=item.variables,
        )
        for item in items
        if item.active
    ]
    settings = get_settings()
    result = submit_message_templates(
        payloads,
        access_token=settings.whatsapp_cloud_access_token,
        waba_id=settings.whatsapp_business_account_id,
        post_json=default_post_json,
        dry_run=dry_run,
    )
    await record_audit(
        db,
        school_id,
        user.id,
        "alert.templates_submit",
        "alert_message_template",
        None,
        {"status": result["status"], "count": len(result["submitted"]), "dry_run": dry_run},
    )
    return TemplateSubmitOut(**result)


@admin_router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateCreate,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    row = AlertMessageTemplate(
        school_id=school_id,
        event_key=body.event_key,
        locale=body.locale,
        whatsapp_template_name=body.whatsapp_template_name,
        language_code=body.language_code,
        variables=body.variables,
        sms_dlt_template_id=body.sms_dlt_template_id,
        sms_text=body.sms_text,
        body_text=body.body_text,
        category="UTILITY",
        active=body.active,
    )
    db.add(row)
    await db.flush()
    await record_audit(
        db,
        school_id,
        user.id,
        "alert.template_created",
        "alert_message_template",
        row.id,
        {"event_key": row.event_key, "locale": row.locale},
    )
    return _template_out(row, source="school")


@admin_router.put("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: UUID,
    body: TemplateUpdate,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    from src.modules.alerts.service import _scope_school

    school_id = _school_id(user)
    await _scope_school(db, school_id)
    row = await db.get(AlertMessageTemplate, template_id)
    if row is None or (row.school_id is not None and row.school_id != school_id):
        raise NotFoundError("Template not found")
    if row.school_id is None:
        existing = (
            await db.execute(
                select(AlertMessageTemplate).where(
                    AlertMessageTemplate.school_id == school_id,
                    AlertMessageTemplate.event_key == row.event_key,
                    AlertMessageTemplate.locale == (body.locale or row.locale),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            row = existing
        else:
            row = AlertMessageTemplate(
                school_id=school_id,
                event_key=row.event_key,
                locale=body.locale or row.locale,
                whatsapp_template_name=row.whatsapp_template_name,
                language_code=row.language_code,
                variables=list(row.variables or []),
                sms_dlt_template_id=row.sms_dlt_template_id,
                sms_text=row.sms_text,
                body_text=row.body_text,
                category=row.category,
                active=row.active,
            )
            db.add(row)
            await db.flush()
    data = body.model_dump(exclude_unset=True)
    data.pop("locale", None)
    for key, value in data.items():
        setattr(row, key, value)
    await db.flush()
    await record_audit(
        db,
        school_id,
        user.id,
        "alert.template_updated",
        "alert_message_template",
        row.id,
        {"event_key": row.event_key, "locale": row.locale},
    )
    return _template_out(row, source="school")


async def _owned_delivery(db: AsyncSession, school_id: UUID, delivery_id: UUID) -> NotificationDelivery:
    row = await db.get(NotificationDelivery, delivery_id)
    if row is None or row.school_id != school_id:
        raise NotFoundError("Delivery not found")
    return row
