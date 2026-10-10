from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
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

router = APIRouter(prefix="/internal/v1", tags=["internal"])


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not x_internal_token or x_internal_token not in settings.internal_service_tokens:
        raise UnauthorizedError("Invalid internal token")


@router.get("/students/ai-context", response_model=AIContextResponse, dependencies=[Depends(require_internal_token)])
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
    from sqlalchemy import select

    from src.db.models import School
    from src.modules.fees.tenant import bind_tenant

    await bind_tenant(db, None, role="SERVICE")
    school_ids = (await db.execute(select(School.id))).scalars().all()
    results = []
    for school_id in school_ids:
        await bind_tenant(db, school_id, role="SERVICE")
        reminders = await FeeService(db).send_invoice_reminders(school_id)
        late = await FeeService(db).apply_late_fees(school_id, None)
        results.append(
            {"school_id": str(school_id), "reminders": reminders, "late_fees_applied": late}
        )
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
