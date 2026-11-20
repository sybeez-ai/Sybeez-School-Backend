"""Tell an employee their payslip is published: in-app, and SMS, WhatsApp, or email when they opted in."""

from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.jobs import enqueue_job
from src.db.models import NotificationDelivery, NotificationPreference, User
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
    try:
        from src.modules.alerts.service import dispatch_alert, school_display_name

        employee = await db.get(User, user_id)
        await dispatch_alert(
            db,
            school_id=school_id,
            event_key="payslip_published",
            notification_type=NotificationType.PAYSLIP_PUBLISHED.value,
            staff_user_ids=[user_id],
            variables={
                "employee_name": employee.full_name if employee else "Employee",
                "period_label": period_label,
                "net_pay": net,
                "school_name": await school_display_name(db, school_id),
            },
            dedupe_key=f"payslip_published:{payslip_id}",
            title=title,
            link_path=link,
        )
    except Exception:
        from src.core.telemetry import track_alert

        track_alert("payroll_notify_failed", channel="WHATSAPP", payslip_id=str(payslip_id))
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


async def notify_pay_run_approved(
    db: AsyncSession,
    *,
    school_id: UUID,
    run_id: UUID,
    period_label: str,
    employee_count: int,
) -> None:
    from src.db.models import SchoolMembership
    from src.modules.alerts.service import PAYROLL_STAFF_ROLES, dispatch_alert, school_display_name

    user_ids = list(
        dict.fromkeys(
            (
                await db.execute(
                    select(SchoolMembership.user_id).where(
                        SchoolMembership.school_id == school_id,
                        SchoolMembership.status == "ACTIVE",
                        SchoolMembership.role.in_(PAYROLL_STAFF_ROLES),
                    )
                )
            ).scalars().all()
        )
    )
    if not user_ids:
        return
    try:
        await dispatch_alert(
            db,
            school_id=school_id,
            event_key="pay_run_approved",
            notification_type=NotificationType.PAY_RUN_APPROVED.value,
            staff_user_ids=user_ids,
            variables={
                "period_label": period_label,
                "employee_count": str(employee_count),
                "school_name": await school_display_name(db, school_id),
            },
            dedupe_key=f"pay_run_approved:{run_id}",
            title="Pay run approved",
            link_path="/hrm/payroll",
        )
    except Exception:
        from src.core.telemetry import track_alert

        track_alert("payroll_notify_failed", channel="WHATSAPP", run_id=str(run_id))


async def notify_lop_update(
    db: AsyncSession,
    *,
    school_id: UUID,
    user_id: UUID | None,
    payslip_id: UUID,
    employee_name: str,
    period_label: str,
    lop_days: str,
) -> None:
    if user_id is None:
        return
    from src.modules.alerts.service import dispatch_alert, school_display_name

    try:
        await dispatch_alert(
            db,
            school_id=school_id,
            event_key="lop_update",
            notification_type=NotificationType.LOP_UPDATE.value,
            staff_user_ids=[user_id],
            variables={
                "employee_name": employee_name,
                "period_label": period_label,
                "lop_days": lop_days,
                "school_name": await school_display_name(db, school_id),
            },
            dedupe_key=f"lop_update:{payslip_id}",
            title="Loss of pay updated",
            link_path="/hrm/payslips",
        )
    except Exception:
        from src.core.telemetry import track_alert

        track_alert("payroll_notify_failed", channel="WHATSAPP", payslip_id=str(payslip_id))
