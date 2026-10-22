import logging
from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import UnauthorizedError
from pydantic import BaseModel

from src.modules.internal.schemas import AIContextResponse
from src.modules.internal.service import InternalAIContextService
from src.modules.notifications.deliveries import update_delivery_result
from src.modules.fees.service import FeeService
from src.modules.attendance.service import AttendanceService
from src.modules.payroll.schemas import InternalGenerate
from src.modules.payroll.service import generate_locked_period
from src.modules.access.deps import service_auth

router = APIRouter(prefix="/internal/v1", tags=["internal"])


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not x_internal_token or x_internal_token not in settings.internal_service_tokens:
        raise UnauthorizedError("Invalid internal token")


require_internal_token.__service_auth__ = True


@router.get("/students/ai-context", response_model=AIContextResponse, dependencies=[Depends(require_internal_token)])
@service_auth
async def student_ai_context(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: UUID = Query(...),
    school_id: UUID = Query(...),
    subject_id: UUID | None = Query(None),
):
    from src.modules.fees.tenant import bind_tenant

    await bind_tenant(db, school_id, user_id=user_id, role="SERVICE")
    return await InternalAIContextService(db).build(user_id=user_id, school_id=school_id, subject_id=subject_id)


class DeliveryResultIn(BaseModel):
    status: str
    provider_message_id: str | None = None
    last_error: str | None = None
    attempts: int | None = None
    error_code: str | None = None
    scheduled_for: datetime | None = None


@router.post(
    "/deliveries/{delivery_id}/result",
    dependencies=[Depends(require_internal_token)],
)
async def delivery_result(
    delivery_id: UUID,
    body: DeliveryResultIn,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.db.models.notification_delivery import NotificationDelivery
    from src.modules.fees.tenant import bind_tenant, set_rls_bypass

    await set_rls_bypass(db, True)
    delivery = await db.get(NotificationDelivery, delivery_id)
    await bind_tenant(
        db,
        None if delivery is None else delivery.school_id,
        user_id=None if delivery is None else delivery.user_id,
        role="SERVICE",
    )
    await update_delivery_result(
        db,
        delivery_id=delivery_id,
        status=body.status,
        provider_message_id=body.provider_message_id,
        last_error=body.last_error,
        attempts=body.attempts,
        error_code=body.error_code,
        scheduled_for=body.scheduled_for,
    )
    return {"ok": True}


@router.post(
    "/fees/{school_id}/reminders/run",
    dependencies=[Depends(require_internal_token)],
)
async def internal_run_fee_reminders(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.modules.fees.tenant import bind_tenant

    await bind_tenant(db, school_id, role="SERVICE")
    reminders = await FeeService(db).send_invoice_reminders(school_id)
    late = await FeeService(db).apply_late_fees(school_id, None)
    return {"reminders": reminders, "late_fees_applied": late}


@router.post(
    "/fees/reminders/run-all",
    dependencies=[Depends(require_internal_token)],
)
async def internal_run_all_fee_reminders(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    from src.db.models import School
    from src.modules.fees.tenant import bind_tenant

    await bind_tenant(db, None, role="SERVICE")
    school_ids = (await db.execute(select(School.id))).scalars().all()
    results = []
    for school_id in school_ids:
        try:
            await bind_tenant(db, school_id, role="SERVICE")
            reminders = await FeeService(db).send_invoice_reminders(school_id)
            late = await FeeService(db).apply_late_fees(school_id, None) if not reminders.get("outside_window") else 0
            results.append({"school_id": str(school_id), "reminders": reminders, "late_fees_applied": late})
        except Exception:  # one school must never stop the others
            await db.rollback()
            results.append({"school_id": str(school_id), "error": True})
    return {"schools": len(results), "results": results}


@router.post(
    "/attendance/{school_id}/digests/absence/run",
    dependencies=[Depends(require_internal_token)],
)
async def internal_run_absence_digests(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    attended_on: date = Query(default_factory=date.today),
):
    from src.modules.fees.tenant import bind_tenant

    await bind_tenant(db, school_id, role="SERVICE")
    return await AttendanceService(db).send_daily_absence_digests(school_id=school_id, attended_on=attended_on)


@router.post("/alerts/release-due", dependencies=[Depends(require_internal_token)])
async def release_due_alerts(db: Annotated[AsyncSession, Depends(get_db)]):
    from src.modules.alerts.service import release_due_deliveries

    return await release_due_deliveries(db)


@router.post("/attendance/{school_id}/digests/weekly/run", dependencies=[Depends(require_internal_token)])
async def internal_run_weekly_attendance(
    school_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    week_ending: date = Query(default_factory=date.today),
):
    return await AttendanceService(db).send_weekly_attendance_summaries(school_id=school_id, week_ending=week_ending)


@router.post("/payroll/generate", dependencies=[Depends(require_internal_token)])
async def internal_generate_payroll(body: InternalGenerate, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await generate_locked_period(
        db,
        school_id=body.school_id,
        actor_id=body.locked_by,
        period_start=body.period_start,
        period_end=body.period_end,
        source=body.source or "ATTENDANCE_LOCK",
    )
    return {"run_id": str(result["run"].id), "created": result["created"], "skipped": result["skipped"]}


@router.post("/online-tests/maintenance", dependencies=[Depends(require_internal_token)])
async def online_tests_maintenance(db: Annotated[AsyncSession, Depends(get_db)]):
    """Worker tick: finalise expired attempts, close ended tests, auto-release results, send reminders."""
    from src.modules.fees.tenant import set_rls_bypass
    from src.modules.online_tests.service import OnlineTestMaintenance

    await set_rls_bypass(db, True)
    return await OnlineTestMaintenance(db).run()


@router.post(
    "/parents/daily-summary/run-all",
    dependencies=[Depends(require_internal_token)],
)
async def internal_run_daily_summaries(db: Annotated[AsyncSession, Depends(get_db)]):
    """Evening 'how was my child's day' summaries. Each school is handled in its own local window."""
    from src.db.models import School
    from src.modules.fees.tenant import bind_tenant
    from src.modules.parent_messages.daily_summary import run_for_school

    await bind_tenant(db, None, role="SERVICE")
    school_ids = (await db.execute(select(School.id))).scalars().all()
    sent = 0
    for school_id in school_ids:
        try:
            await bind_tenant(db, school_id, role="SERVICE")
            sent += (await run_for_school(db, school_id)).get("sent", 0)
        except Exception:
            await db.rollback()
            logging.getLogger("sybeez.daily_summary").exception("daily summary failed for a school")
    return {"schools": len(school_ids), "sent": sent}


@router.post("/cognito/sync-groups/run-all", dependencies=[Depends(require_internal_token)])
async def internal_sync_cognito_groups(db: Annotated[AsyncSession, Depends(get_db)], limit: int = Query(500, ge=1, le=2000)):
    """Reconcile Cognito groups with database roles for users that live in the pool. Groups are informational only."""
    from src.db.models import User
    from src.modules.auth import cognito as cognito_svc
    from src.modules.fees.tenant import set_rls_bypass
    from src.modules.schools.provisioning import sync_cognito_groups

    if not cognito_svc.cognito_configured():
        return {"checked": 0, "synced": 0, "skipped": "cognito not configured"}
    await set_rls_bypass(db, True)
    users = (
        await db.execute(select(User).where(User.cognito_sub.is_not(None), User.is_active.is_(True)).limit(limit))
    ).scalars().all()
    ok = 0
    for user in users:
        ok += 1 if await sync_cognito_groups(db, user) else 0
    return {"checked": len(users), "synced": ok}
