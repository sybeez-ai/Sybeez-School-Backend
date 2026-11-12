"""School and platform admin controls, plus the optional one-time link code.

Sending stays an environment switch. These routes cannot turn it on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user, require_roles, require_school_scope
from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.db.models import WhatsAppAssistantSettings, WhatsAppMessage, WhatsAppSession, WhatsAppUsage
from src.modules.access.deps import service_auth
from src.modules.alerts.phone import mask_phone
from src.modules.alerts.service import enable_rls_bypass
from src.modules.fees.tenant import bind_tenant
from src.modules.internal.router import require_internal_token
from src.modules.whatsapp_assistant.schemas import (
    AssistantSettingsOut,
    AssistantSettingsUpdate,
    AssistantStatusOut,
    InboundJob,
    LinkCodeOut,
    TranscriptItem,
    TranscriptList,
    UnlinkOut,
)
from src.modules.whatsapp_assistant.scope import transcript_school
from src.modules.whatsapp_assistant.service import WhatsAppAssistant
from src.shared.enums import UserRole

router = APIRouter(prefix="/whatsapp-assistant", tags=["whatsapp-assistant"])
internal_router = APIRouter(prefix="/internal/v1/whatsapp-assistant", tags=["whatsapp-assistant"])

Db = Annotated[AsyncSession, Depends(get_db)]
SignedIn = Annotated[CurrentUser, Depends(get_current_user)]
Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.PLATFORM_ADMIN))]
WEBHOOK_PATH = "/api/v1/webhooks/whatsapp/meta"


def _status_payload(school: AssistantSettingsOut | None, usage: dict) -> AssistantStatusOut:
    settings = get_settings()
    missing = []
    if not settings.whatsapp_cloud_access_token:
        missing.append("WHATSAPP_ACCESS_TOKEN")
    if not settings.whatsapp_cloud_phone_number_id:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not settings.whatsapp_app_secret:
        missing.append("WHATSAPP_APP_SECRET")
    if not settings.whatsapp_verify_token:
        missing.append("WHATSAPP_VERIFY_TOKEN")
    configured = bool(settings.whatsapp_cloud_access_token and settings.whatsapp_cloud_phone_number_id)
    return AssistantStatusOut(
        sending_enabled=bool(settings.whatsapp_sending_enabled),
        provider_configured=configured,
        phone_number_id_set=bool(settings.whatsapp_cloud_phone_number_id),
        webhook_path=WEBHOOK_PATH,
        school=school,
        usage=usage,
        missing=missing,
    )


async def _settings_out(db: AsyncSession, school_id: UUID) -> AssistantSettingsOut:
    row = await db.get(WhatsAppAssistantSettings, school_id)
    if row is None:
        return AssistantSettingsOut(school_id=school_id, assistant_enabled=False, leave_requests_enabled=False)
    return AssistantSettingsOut(
        school_id=school_id,
        assistant_enabled=row.assistant_enabled,
        leave_requests_enabled=row.leave_requests_enabled,
    )


async def _usage(db: AsyncSession, school_id: UUID) -> dict:
    period = datetime.now(timezone.utc).strftime("%Y-%m")
    row = (
        await db.execute(
            select(WhatsAppUsage).where(WhatsAppUsage.school_id == school_id, WhatsAppUsage.year_month == period)
        )
    ).scalar_one_or_none()
    if row is None:
        return {"year_month": period, "inbound": 0, "generated": 0, "sent": 0, "suppressed": 0}
    return {
        "year_month": period,
        "inbound": row.inbound_count,
        "generated": row.generated_count,
        "sent": row.sent_count,
        "suppressed": row.suppressed_count,
    }


@router.post("/link-code", response_model=LinkCodeOut)
async def issue_link_code(db: Db, user: SignedIn):
    return await WhatsAppAssistant(db).issue_link_code(user)


@router.delete("/link", response_model=UnlinkOut)
async def unlink_number(db: Db, user: SignedIn):
    return await WhatsAppAssistant(db).unlink(user)


@router.get("/status", response_model=AssistantStatusOut)
async def assistant_status(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    if user.school_id is None:
        return _status_payload(None, {})
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    school = await _settings_out(db, user.school_id)
    return _status_payload(school, await _usage(db, user.school_id))


@router.put("/settings", response_model=AssistantSettingsOut)
async def update_settings(
    body: AssistantSettingsUpdate,
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
):
    if user.school_id is None:
        raise ForbiddenError("Pass X-School-Id for this school")
    await bind_tenant(db, user.school_id, user_id=user.id, role=user.role.value)
    row = await db.get(WhatsAppAssistantSettings, user.school_id)
    if row is None:
        row = WhatsAppAssistantSettings(school_id=user.school_id)
        db.add(row)
    if body.assistant_enabled is not None:
        row.assistant_enabled = body.assistant_enabled
    if body.leave_requests_enabled is not None:
        row.leave_requests_enabled = body.leave_requests_enabled
    await db.flush()
    return AssistantSettingsOut(
        school_id=user.school_id,
        assistant_enabled=row.assistant_enabled,
        leave_requests_enabled=row.leave_requests_enabled,
    )


@router.get("/transcripts", response_model=TranscriptList)
async def transcripts(
    db: Db,
    user: Admin,
    _scope: Annotated[CurrentUser, Depends(require_school_scope)],
    school_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        visible = transcript_school(
            actor_role=user.role.value,
            actor_school=user.school_id,
            requested=school_id,
            is_platform_admin=user.role == UserRole.PLATFORM_ADMIN or user.is_platform_admin,
        )
    except PermissionError as exc:
        raise ForbiddenError("Cross-tenant access denied") from exc
    if user.role == UserRole.PLATFORM_ADMIN or user.is_platform_admin:
        await enable_rls_bypass(db)
    elif visible is not None:
        await bind_tenant(db, visible, user_id=user.id, role=user.role.value)
    filters = []
    if visible is not None:
        filters.append(WhatsAppMessage.school_id == visible)
    total = int((await db.execute(select(func.count()).select_from(WhatsAppMessage).where(*filters))).scalar_one())
    rows = (
        await db.execute(
            select(WhatsAppMessage, WhatsAppSession.phone_e164)
            .outerjoin(WhatsAppSession, WhatsAppSession.id == WhatsAppMessage.session_id)
            .where(*filters)
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [
        TranscriptItem(
            id=message.id,
            school_id=message.school_id,
            user_id=message.user_id,
            direction=message.direction,
            body=message.body,
            status=message.status,
            persona=message.persona,
            send_reason=message.send_reason,
            phone_masked=mask_phone(phone),
            created_at=message.created_at,
        )
        for message, phone in rows
    ]
    return TranscriptList(items=items, total=total)


@internal_router.post("/inbound", dependencies=[Depends(require_internal_token)])
@service_auth
async def inbound_job(body: InboundJob, db: Db):
    return await WhatsAppAssistant(db).handle_meta_body(body.body)
