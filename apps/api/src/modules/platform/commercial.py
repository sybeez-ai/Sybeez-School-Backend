"""Per-school plan price, discount, trial and renewal.

Pure helpers are covered by unit tests. Loaders run inside a request that has
already bound the school (or turned row-level security bypass on).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError, ValidationError
from src.db.models import PlatformPlan, PlatformSettings, School, Student, Subscription
from src.modules.audit.repository import AuditRepository
from src.shared.enums import PersonStatus, SubscriptionStatus
from src.shared.utils import utcnow


def money(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return round(float(value), 2)


def quote(
    price_per_student: float,
    student_count: int,
    discount_percent: float | None,
    discount_amount: float | None,
) -> dict[str, float]:
    """Gross, discount taken off, and the amount the school is billed."""
    students = max(int(student_count or 0), 0)
    gross = round(max(price_per_student, 0) * students, 2)
    percent = max(float(discount_percent or 0), 0)
    amount = max(float(discount_amount or 0), 0)
    percent_off = round(gross * percent / 100, 2)
    discount_value = round(percent_off + amount, 2)
    total = round(max(0.0, gross - discount_value), 2)
    return {"gross_amount": gross, "discount_value": discount_value, "total_amount": total}


def billing_status(
    *,
    subscription_status: str | None,
    trial_ends_at: datetime | None,
    renewal_at: datetime | None,
    now: datetime | None = None,
) -> str:
    """active, trial, or expired. Anything that is not a live trial or paid plan is expired."""
    now = now or utcnow()
    status = (subscription_status or "").upper()
    if status == SubscriptionStatus.TRIAL.value:
        if trial_ends_at is not None and trial_ends_at < now:
            return "expired"
        return "trial"
    if status == SubscriptionStatus.ACTIVE.value:
        if renewal_at is not None and renewal_at < now:
            return "expired"
        return "active"
    return "expired"


def days_left(ends_at: datetime | None, now: datetime | None = None) -> int:
    now = now or utcnow()
    if ends_at is None or ends_at <= now:
        return 0
    return max(0, int((ends_at - now).total_seconds() // 86400) + (1 if (ends_at - now).total_seconds() % 86400 else 0))


def end_of_day(day) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


async def default_trial_days(db: AsyncSession) -> int:
    row = (await db.execute(select(PlatformSettings).limit(1))).scalar_one_or_none()
    return int(row.default_trial_days) if row and row.default_trial_days else 30


async def latest_subscription(db: AsyncSession, school_id: UUID) -> Subscription | None:
    return (
        await db.execute(
            select(Subscription)
            .where(Subscription.school_id == school_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _plan(db: AsyncSession, code: str | None) -> PlatformPlan | None:
    if not code:
        return None
    return (
        await db.execute(select(PlatformPlan).where(PlatformPlan.code == code.lower()))
    ).scalar_one_or_none()


async def active_student_count(db: AsyncSession, school_id: UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.count())
                .select_from(Student)
                .where(Student.school_id == school_id, Student.status == PersonStatus.ACTIVE.value)
            )
        ).scalar_one()
    )


def effective_price(sub: Subscription | None, plan: PlatformPlan | None) -> tuple[float, str]:
    if sub is not None and sub.price_per_student is not None:
        return money(sub.price_per_student), "school"
    if plan is not None and plan.price_monthly is not None:
        return money(plan.price_monthly), "plan"
    return 0.0, "school"


async def commercial_view(db: AsyncSession, school: School) -> dict[str, Any]:
    sub = await latest_subscription(db, school.id)
    plan = await _plan(db, sub.plan_code if sub else None)
    students = await active_student_count(db, school.id)
    price, price_source = effective_price(sub, plan)
    percent = money(sub.discount_percent) if sub and sub.discount_percent is not None else None
    amount = money(sub.discount_amount) if sub and sub.discount_amount is not None else None
    totals = quote(price, students, percent, amount)
    trial_end = sub.trial_ends_at if sub else None
    renewal = sub.renewal_at if sub else None
    if sub and sub.status == SubscriptionStatus.TRIAL.value and trial_end is None:
        trial_end = sub.ends_at
    if sub and sub.status == SubscriptionStatus.ACTIVE.value and renewal is None:
        renewal = sub.ends_at
    status = billing_status(
        subscription_status=sub.status if sub else None,
        trial_ends_at=trial_end,
        renewal_at=renewal,
    )
    trial_start = None
    if sub is not None:
        trial_start = sub.trial_started_at or (sub.starts_at if sub.status == SubscriptionStatus.TRIAL.value else None)
    return {
        "plan_code": sub.plan_code if sub else None,
        "plan_name": plan.name if plan else (sub.plan_code if sub else None),
        "price_per_student": price,
        "price_source": price_source,
        "currency": school.currency or (plan.currency if plan else None) or "INR",
        "student_count": students,
        "discount_percent": percent,
        "discount_amount": amount,
        "discount_reason": sub.discount_reason if sub else None,
        "plan_started_at": sub.starts_at if sub else None,
        "renewal_at": renewal,
        "billing_status": status,
        "on_trial": status == "trial",
        "trial_started_at": trial_start,
        "trial_ends_at": trial_end,
        "trial_days_left": days_left(trial_end) if status == "trial" else 0,
        **totals,
    }


def _dec(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 2)))


async def apply_commercial(
    db: AsyncSession,
    *,
    actor_id: UUID,
    school: School,
    plan_code: str | None = None,
    price_per_student: float | None = None,
    discount_percent: float | None = None,
    discount_amount: float | None = None,
    discount_reason: str | None = None,
    clear_discount_percent: bool = False,
    clear_discount_amount: bool = False,
    clear_discount_reason: bool = False,
    renewal_at: datetime | None = None,
    starts_at: datetime | None = None,
    fields_set: set[str] | None = None,
) -> dict[str, Any]:
    """Update the school's current subscription. Missing keys are left alone."""
    fields_set = fields_set or set()
    sub = await latest_subscription(db, school.id)
    if sub is None:
        if not plan_code:
            raise NotFoundError("This school has no subscription yet")
        sub = Subscription(
            school_id=school.id,
            plan_code=plan_code.lower(),
            status=SubscriptionStatus.ACTIVE.value,
            starts_at=starts_at or utcnow(),
        )
        db.add(sub)
        await db.flush()
    before = await commercial_view(db, school)
    if plan_code:
        plan = await _plan(db, plan_code)
        if plan is None:
            raise NotFoundError("Plan not found")
        sub.plan_code = plan.code
    if "price_per_student" in fields_set:
        if price_per_student is None:
            sub.price_per_student = None
        elif price_per_student < 0:
            raise ValidationError("Price per student cannot be negative")
        else:
            sub.price_per_student = _dec(price_per_student)
    if "discount_percent" in fields_set:
        if clear_discount_percent or discount_percent is None:
            sub.discount_percent = None
        elif not 0 <= discount_percent <= 100:
            raise ValidationError("Discount percent must be between 0 and 100")
        else:
            sub.discount_percent = _dec(discount_percent)
    if "discount_amount" in fields_set:
        if clear_discount_amount or discount_amount is None:
            sub.discount_amount = None
        elif discount_amount < 0:
            raise ValidationError("Discount amount cannot be negative")
        else:
            sub.discount_amount = _dec(discount_amount)
    if "discount_reason" in fields_set:
        sub.discount_reason = None if clear_discount_reason or not discount_reason else discount_reason.strip()
    if "renewal_at" in fields_set:
        sub.renewal_at = renewal_at
        if sub.status == SubscriptionStatus.ACTIVE.value:
            sub.ends_at = renewal_at
    if "starts_at" in fields_set and starts_at is not None:
        sub.starts_at = starts_at
    await db.flush()
    after = await commercial_view(db, school)
    await AuditRepository(db).write(
        action="platform.subscription.update",
        actor_id=actor_id,
        school_id=school.id,
        resource_type="subscription",
        resource_id=str(sub.id),
        message=f"Updated plan terms for {school.name}",
        details={"before": _audit_slice(before), "after": _audit_slice(after)},
    )
    return after


def _audit_slice(view: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "plan_code",
        "plan_name",
        "price_per_student",
        "price_source",
        "discount_percent",
        "discount_amount",
        "discount_reason",
        "student_count",
        "total_amount",
        "billing_status",
        "renewal_at",
        "plan_started_at",
        "trial_started_at",
        "trial_ends_at",
    )
    out = {}
    for key in keys:
        value = view.get(key)
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out

