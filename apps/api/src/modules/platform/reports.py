"""CSV downloads for the platform console. Token reports are counts only — no prices."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, is_super_admin
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import AuditLog, PlatformAiUsageDaily, PlatformAiUserUsage, School, Subscription, User
from src.modules.platform.console import category_of

MEMBER_REPORTS = frozenset({"schools", "trials", "renewals", "ai-tokens"})
SUPER_ADMIN_REPORTS = frozenset({"billing", "sign-ins", "logs"})
LOG_EXPORT_LIMIT = 10000

COLUMNS: dict[str, list[str]] = {
    "schools": ["school", "code", "status", "email", "phone", "city", "state", "country", "created_at"],
    "trials": ["school", "plan", "status", "trial_started_at", "trial_ends_at"],
    "renewals": ["school", "plan", "status", "renewal_at"],
    "billing": ["school", "plan", "status", "starts_at", "ends_at", "renewal_at", "trial_started_at", "trial_ends_at"],
    "ai-tokens": ["school", "model", "tokens", "user"],
    "sign-ins": ["email", "name", "last_login_at"],
    "logs": ["created_at", "category", "action", "message", "school", "actor_email", "ip_address"],
}


def report_audience(kind: str) -> str:
    key = (kind or "").strip().lower()
    if key in MEMBER_REPORTS:
        return "member"
    if key in SUPER_ADMIN_REPORTS:
        return "super"
    raise NotFoundError("Report not found")


def assert_report_access(kind: str, user: CurrentUser) -> str:
    audience = report_audience(kind)
    if audience == "super" and not is_super_admin(user):
        raise ForbiddenError("Super admin only")
    return audience


def render_csv(headers: list[str], rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in headers})
    return buffer.getvalue()


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def ai_token_export_rows(daily: list[tuple], people: list[tuple]) -> list[dict]:
    """User rows when a person is known, plus any school/model tokens that are not on a person.

    daily: (school, model, tokens)
    people: (school, model, tokens, user)
    """
    attributed: dict[tuple[str, str], int] = {}
    rows: list[dict] = []
    for school, model, tokens, user in people:
        count = int(tokens or 0)
        if count <= 0:
            continue
        school_name = school or "Platform"
        model_name = model or "unknown"
        rows.append(
            {"school": school_name, "model": model_name, "tokens": count, "user": (user or "").strip()}
        )
        key = (school_name, model_name)
        attributed[key] = attributed.get(key, 0) + count
    for school, model, tokens in daily:
        count = int(tokens or 0)
        school_name = school or "Platform"
        model_name = model or "unknown"
        leftover = count - attributed.get((school_name, model_name), 0)
        if leftover > 0:
            rows.append({"school": school_name, "model": model_name, "tokens": leftover, "user": ""})
    rows.sort(key=lambda item: (item["school"].lower(), item["model"].lower(), item["user"].lower()))
    return rows


def _trial_start(sub: Subscription) -> datetime | None:
    if sub.trial_started_at is not None:
        return sub.trial_started_at
    if sub.status == "TRIAL":
        return sub.starts_at
    return None


def _trial_end(sub: Subscription) -> datetime | None:
    if sub.trial_ends_at is not None:
        return sub.trial_ends_at
    if sub.status == "TRIAL":
        return sub.ends_at
    return None


async def _schools(db: AsyncSession) -> list[dict]:
    schools = (await db.execute(select(School).order_by(School.name.asc()))).scalars().all()
    return [
        {
            "school": school.name,
            "code": school.code or "",
            "status": school.status,
            "email": school.email or "",
            "phone": school.phone or "",
            "city": school.city or "",
            "state": school.state or "",
            "country": school.country or "",
            "created_at": _iso(school.created_at),
        }
        for school in schools
    ]


async def _subscriptions(db: AsyncSession, *, trials: bool = False, renewals: bool = False) -> list[tuple]:
    stmt = select(School.name, Subscription).join(Subscription, Subscription.school_id == School.id)
    if trials:
        stmt = stmt.where(or_(Subscription.status == "TRIAL", Subscription.trial_ends_at.is_not(None)))
    if renewals:
        stmt = stmt.where(Subscription.renewal_at.is_not(None))
    stmt = stmt.order_by(School.name.asc(), Subscription.created_at.asc())
    return list((await db.execute(stmt)).all())


async def _ai_tokens(db: AsyncSession) -> list[dict]:
    daily = (
        await db.execute(
            select(
                School.name,
                PlatformAiUsageDaily.model,
                func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens),
            )
            .outerjoin(School, School.id == PlatformAiUsageDaily.school_id)
            .group_by(School.name, PlatformAiUsageDaily.model)
        )
    ).all()
    people = (
        await db.execute(
            select(School.name, PlatformAiUserUsage.model, PlatformAiUserUsage.tokens, User.full_name, User.email)
            .join(School, School.id == PlatformAiUserUsage.school_id)
            .outerjoin(User, User.id == PlatformAiUserUsage.user_id)
        )
    ).all()
    person_rows = [
        (school, model, tokens, (name or "").strip() or (email or "").strip())
        for school, model, tokens, name, email in people
    ]
    return ai_token_export_rows(list(daily), person_rows)


async def _sign_ins(db: AsyncSession) -> list[dict]:
    users = (
        await db.execute(
            select(User).where(User.last_login_at.is_not(None)).order_by(User.last_login_at.desc(), User.email.asc())
        )
    ).scalars().all()
    return [
        {"email": user.email, "name": user.full_name, "last_login_at": _iso(user.last_login_at)}
        for user in users
    ]


async def _logs(db: AsyncSession) -> list[dict]:
    rows = (
        await db.execute(
            select(AuditLog, User.email, School.name)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .outerjoin(School, School.id == AuditLog.school_id)
            .order_by(AuditLog.created_at.desc())
            .limit(LOG_EXPORT_LIMIT)
        )
    ).all()
    return [
        {
            "created_at": _iso(log.created_at),
            "category": category_of(log.action),
            "action": log.action,
            "message": log.message or "",
            "school": school_name or "",
            "actor_email": email or "",
            "ip_address": log.ip_address or "",
        }
        for log, email, school_name in rows
    ]


async def render_report(db: AsyncSession, kind: str) -> str:
    from src.modules.fees.tenant import set_rls_bypass

    key = kind.strip().lower()
    report_audience(key)
    await set_rls_bypass(db, True)
    headers = COLUMNS[key]
    if key == "schools":
        rows = await _schools(db)
    elif key == "trials":
        rows = [
            {
                "school": name,
                "plan": sub.plan_code,
                "status": sub.status,
                "trial_started_at": _iso(_trial_start(sub)),
                "trial_ends_at": _iso(_trial_end(sub)),
            }
            for name, sub in await _subscriptions(db, trials=True)
        ]
    elif key == "renewals":
        rows = [
            {"school": name, "plan": sub.plan_code, "status": sub.status, "renewal_at": _iso(sub.renewal_at)}
            for name, sub in await _subscriptions(db, renewals=True)
        ]
    elif key == "billing":
        rows = [
            {
                "school": name,
                "plan": sub.plan_code,
                "status": sub.status,
                "starts_at": _iso(sub.starts_at),
                "ends_at": _iso(sub.ends_at),
                "renewal_at": _iso(sub.renewal_at),
                "trial_started_at": _iso(_trial_start(sub)),
                "trial_ends_at": _iso(_trial_end(sub)),
            }
            for name, sub in await _subscriptions(db)
        ]
    elif key == "ai-tokens":
        rows = await _ai_tokens(db)
    elif key == "sign-ins":
        rows = await _sign_ins(db)
    else:
        rows = await _logs(db)
    return render_csv(headers, rows)
