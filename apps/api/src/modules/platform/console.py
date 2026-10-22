"""Platform console extras: logs, bell notifications, school members, trials and billing prompt.

* Logs read audit_logs across every school (activity, login failures, app errors), super admin only.
* Bell notifications are derived from audit_logs; only read state lives in platform_notification_reads.
* Trials use the latest TRIAL subscription (ends_at); a school with no subscription gets the platform
  default from its creation date. Nothing here hard-blocks a school: expiry only raises a pay prompt.
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    CurrentUser,
    get_current_user,
    require_platform_member,
    require_super_admin,
)
from src.core.database import get_db
from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import (
    AuditLog,
    PlatformNotificationRead,
    PlatformSchoolAccess,
    School,
    SchoolMembership,
    Subscription,
    User,
)
from src.shared.enums import SubscriptionStatus

router = APIRouter(prefix="/platform", tags=["platform-console"])
billing_router = APIRouter(prefix="/billing", tags=["billing"])

DB = Annotated[AsyncSession, Depends(get_db)]

ERROR_ACTIONS = ("app.error", "app.client_error", "app.access_denied")
LOGIN_FAILED = "auth.login_failed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _read_scope(db: AsyncSession) -> None:
    await db.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))


# ── logs ─────────────────────────────────────────────────────


class LogOut(BaseModel):
    id: UUID
    created_at: datetime
    category: str
    action: str
    message: str | None = None
    school_id: UUID | None = None
    school_name: str | None = None
    actor_id: UUID | None = None
    actor_email: str | None = None
    actor_name: str | None = None
    actor_role: str | None = None
    ip_address: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    details: dict | None = None


class LogPage(BaseModel):
    items: list[LogOut]
    total: int
    page: int
    page_size: int


LogType = Literal["all", "activity", "login_failed", "error", "auth", "platform"]


def category_of(action: str) -> str:
    if action == LOGIN_FAILED:
        return "login_failed"
    if action in ERROR_ACTIONS:
        return "error"
    if action.startswith("auth."):
        return "auth"
    if action.startswith("platform.") or action.startswith("feature.platform"):
        return "platform"
    return "activity"


def _type_filter(kind: str):
    if kind == "login_failed":
        return AuditLog.action == LOGIN_FAILED
    if kind == "error":
        return AuditLog.action.in_(ERROR_ACTIONS)
    if kind == "auth":
        return AuditLog.action.like("auth.%")
    if kind == "platform":
        return or_(AuditLog.action.like("platform.%"), AuditLog.action.like("feature.platform%"))
    if kind == "activity":
        return and_(AuditLog.action.notin_(ERROR_ACTIONS), AuditLog.action != LOGIN_FAILED)
    return None


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


def _filters(
    *,
    school_id: UUID | None,
    actor_id: UUID | None,
    kind: str,
    action: str | None,
    q: str | None,
    date_from: date | None,
    date_to: date | None,
    no_school: bool = False,
):
    conds = []
    if school_id is not None:
        conds.append(AuditLog.school_id == school_id)
    elif no_school:
        conds.append(AuditLog.school_id.is_(None))
    if actor_id is not None:
        conds.append(AuditLog.actor_id == actor_id)
    tf = _type_filter(kind)
    if tf is not None:
        conds.append(tf)
    if action:
        conds.append(AuditLog.action == action)
    if date_from:
        conds.append(AuditLog.created_at >= _day_start(date_from))
    if date_to:
        conds.append(AuditLog.created_at < _day_start(date_to) + timedelta(days=1))
    if q:
        like = f"%{q.strip().lower()}%"
        conds.append(
            or_(
                func.lower(func.coalesce(AuditLog.message, "")).like(like),
                func.lower(func.coalesce(User.email, "")).like(like),
                func.lower(func.coalesce(User.full_name, "")).like(like),
                func.lower(AuditLog.action).like(like),
                func.lower(func.coalesce(AuditLog.details["email"].astext, "")).like(like),
            )
        )
    return conds


@router.get("/logs", response_model=LogPage)
async def list_logs(
    db: DB,
    _: Annotated[CurrentUser, Depends(require_super_admin)],
    school_id: UUID | None = Query(None),
    no_school: bool = Query(False, description="Only platform-level rows (no school)"),
    actor_id: UUID | None = Query(None),
    type: LogType = Query("all"),  # noqa: A002
    action: str | None = Query(None, max_length=128),
    q: str | None = Query(None, max_length=200),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Every school, teacher and user action, login failures and app errors, newest first."""
    await _read_scope(db)
    conds = _filters(
        school_id=school_id, actor_id=actor_id, kind=type, action=action, q=q,
        date_from=date_from, date_to=date_to, no_school=no_school,
    )
    role_sq = (
        select(SchoolMembership.role)
        .where(SchoolMembership.user_id == AuditLog.actor_id, SchoolMembership.school_id == AuditLog.school_id)
        .order_by(SchoolMembership.created_at.asc())
        .limit(1)
        .scalar_subquery()
    )
    base = (
        select(AuditLog, User.email, User.full_name, User.platform_role, School.name, role_sq.label("member_role"))
        .outerjoin(User, User.id == AuditLog.actor_id)
        .outerjoin(School, School.id == AuditLog.school_id)
        .where(*conds)
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(AuditLog)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .where(*conds)
        )
    ).scalar_one()
    rows = (
        await db.execute(base.order_by(desc(AuditLog.created_at)).offset((page - 1) * page_size).limit(page_size))
    ).all()
    items = []
    for log, email, name, platform_role, school_name, member_role in rows:
        details = log.details or None
        items.append(
            LogOut(
                id=log.id,
                created_at=log.created_at,
                category=category_of(log.action),
                action=log.action,
                message=log.message,
                school_id=log.school_id,
                school_name=school_name,
                actor_id=log.actor_id,
                actor_email=email or (details or {}).get("email"),
                actor_name=name,
                actor_role=member_role or (f"PLATFORM:{platform_role}" if platform_role else (details or {}).get("role")),
                ip_address=log.ip_address,
                resource_type=log.resource_type,
                resource_id=log.resource_id,
                details=details,
            )
        )
    return LogPage(items=items, total=int(total), page=page, page_size=page_size)


class SchoolActivityOut(BaseModel):
    school_id: UUID | None
    school_name: str
    events: int
    errors: int
    login_failures: int
    active_users: int
    last_activity_at: datetime | None = None


@router.get("/logs/summary", response_model=list[SchoolActivityOut])
async def logs_summary(
    db: DB,
    _: Annotated[CurrentUser, Depends(require_super_admin)],
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
):
    """Per-school activity: events, errors, login failures and distinct active users in the window."""
    await _read_scope(db)
    start = _day_start(date_from) if date_from else _now() - timedelta(days=7)
    conds = [AuditLog.created_at >= start]
    if date_to:
        conds.append(AuditLog.created_at < _day_start(date_to) + timedelta(days=1))
    is_err = func.sum(func.cast(AuditLog.action.in_(ERROR_ACTIONS), type_=_int()))
    is_fail = func.sum(func.cast(AuditLog.action == LOGIN_FAILED, type_=_int()))
    stats = (
        await db.execute(
            select(
                AuditLog.school_id,
                func.count(),
                is_err,
                is_fail,
                func.count(func.distinct(AuditLog.actor_id)),
                func.max(AuditLog.created_at),
            )
            .where(*conds)
            .group_by(AuditLog.school_id)
        )
    ).all()
    by_school = {sid: row for sid, *row in stats}
    schools = (await db.execute(select(School.id, School.name).order_by(School.name.asc()))).all()
    out = []
    for sid, name in schools:
        row = by_school.get(sid)
        out.append(
            SchoolActivityOut(
                school_id=sid, school_name=name,
                events=int(row[0]) if row else 0, errors=int(row[1] or 0) if row else 0,
                login_failures=int(row[2] or 0) if row else 0, active_users=int(row[3]) if row else 0,
                last_activity_at=row[4] if row else None,
            )
        )
    plat = by_school.get(None)
    if plat:
        out.append(
            SchoolActivityOut(
                school_id=None, school_name="Platform / no school", events=int(plat[0]),
                errors=int(plat[1] or 0), login_failures=int(plat[2] or 0), active_users=int(plat[3]),
                last_activity_at=plat[4],
            )
        )
    out.sort(key=lambda s: (s.last_activity_at is None, -(s.last_activity_at.timestamp() if s.last_activity_at else 0)))
    return out


def _int():
    from sqlalchemy import Integer

    return Integer


@router.get("/logs/actions", response_model=list[str])
async def logs_actions(db: DB, _: Annotated[CurrentUser, Depends(require_super_admin)]):
    await _read_scope(db)
    rows = (await db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action.asc()).limit(500))).scalars()
    return list(rows)


class LogUserOut(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None


@router.get("/logs/users", response_model=list[LogUserOut])
async def logs_users(
    db: DB,
    _: Annotated[CurrentUser, Depends(require_super_admin)],
    q: str = Query(..., min_length=2, max_length=120),
    school_id: UUID | None = Query(None),
):
    """User picker for the logs filter (email or name match)."""
    await _read_scope(db)
    like = f"%{q.strip().lower()}%"
    stmt = select(User.id, User.email, User.full_name).where(
        or_(func.lower(User.email).like(like), func.lower(func.coalesce(User.full_name, "")).like(like))
    )
    if school_id is not None:
        stmt = stmt.where(
            User.id.in_(select(SchoolMembership.user_id).where(SchoolMembership.school_id == school_id))
        )
    rows = (await db.execute(stmt.order_by(User.email.asc()).limit(20))).all()
    return [LogUserOut(id=i, email=e, full_name=n) for i, e, n in rows]


# ── bell notifications ───────────────────────────────────────

NOTIFY_TYPES: dict[str, dict[str, Any]] = {
    "admins": {"label": "School admins added or invited", "match": ["platform.school.admin%", "platform.invitation%"]},
    "schools": {"label": "New schools and school status changes", "match": ["platform.school.%"]},
    "team": {"label": "Team and employee changes", "match": ["platform.employee%", "staff.%"]},
    "plans": {"label": "Plans, trials and subscriptions", "match": ["platform.plan%", "platform.subscription%", "platform.trial%"]},
    "login_failures": {"label": "Failed sign-ins", "match": [LOGIN_FAILED]},
    "errors": {"label": "App errors", "match": list(ERROR_ACTIONS)},
}
WINDOW_DAYS = 30


class PlatformNotificationOut(BaseModel):
    id: UUID
    title: str
    body: str
    category: str
    link_path: str | None = None
    is_read: bool
    created_at: datetime


class PlatformNotificationList(BaseModel):
    items: list[PlatformNotificationOut]
    unread_count: int


class NotificationPrefsOut(BaseModel):
    types: list[dict[str, Any]]


class NotificationPrefsIn(BaseModel):
    enabled: dict[str, bool] = Field(default_factory=dict)


async def _state(db: AsyncSession, user_id: UUID) -> PlatformNotificationRead | None:
    return await db.get(PlatformNotificationRead, user_id)


def _enabled_types(state: PlatformNotificationRead | None) -> list[str]:
    prefs = (state.prefs if state else None) or {}
    return [k for k in NOTIFY_TYPES if prefs.get(k, True)]


def _type_cond(types: list[str]):
    parts = []
    for t in types:
        for pat in NOTIFY_TYPES[t]["match"]:
            parts.append(AuditLog.action.like(pat) if "%" in pat else AuditLog.action == pat)
    return or_(*parts) if parts else AuditLog.id.is_(None)


def _ntype(action: str) -> str:
    for t, meta in NOTIFY_TYPES.items():
        for pat in meta["match"]:
            if (pat.endswith("%") and action.startswith(pat[:-1])) or action == pat:
                return t
    return "activity"


_TITLES = {
    "schools": "School update",
    "admins": "School admin update",
    "team": "Team update",
    "plans": "Plans and billing",
    "login_failures": "Failed sign-in",
    "errors": "App error",
}


def _link(t: str, log: AuditLog) -> str:
    if t in {"login_failures", "errors"}:
        return f"/platform/logs?type={'login_failed' if t == 'login_failures' else 'error'}"
    if t in {"schools", "admins"} and log.school_id:
        return f"/platform/schools/detail?schoolId={log.school_id}"
    if t == "team":
        return "/platform/team"
    if t == "plans":
        return "/platform/plans"
    return "/platform/logs"


async def _notification_rows(db: AsyncSession, user: CurrentUser, limit: int):
    state = await _state(db, user.id)
    types = _enabled_types(state)
    since = _now() - timedelta(days=WINDOW_DAYS)
    cond = [AuditLog.created_at >= since, _type_cond(types)]
    # Your own actions are not news to you.
    cond.append(or_(AuditLog.actor_id.is_(None), AuditLog.actor_id != user.id))
    rows = (
        await db.execute(
            select(AuditLog, School.name)
            .outerjoin(School, School.id == AuditLog.school_id)
            .where(*cond)
            .order_by(desc(AuditLog.created_at))
            .limit(limit)
        )
    ).all()
    read_all = state.read_all_at if state else None
    read_ids = set((state.read_ids if state else None) or [])

    def is_read(log: AuditLog) -> bool:
        return (read_all is not None and log.created_at <= read_all) or str(log.id) in read_ids

    unread_q = select(func.count()).select_from(AuditLog).where(*cond)
    if read_all is not None:
        unread_q = unread_q.where(AuditLog.created_at > read_all)
    unread = int((await db.execute(unread_q)).scalar_one())
    if read_ids:
        extra = select(func.count()).select_from(AuditLog).where(
            *cond, AuditLog.id.in_([UUID(x) for x in read_ids if _is_uuid(x)])
        )
        if read_all is not None:
            extra = extra.where(AuditLog.created_at > read_all)
        unread -= int((await db.execute(extra)).scalar_one())
    return rows, is_read, max(unread, 0)


def _is_uuid(v: str) -> bool:
    try:
        UUID(str(v))
        return True
    except ValueError:
        return False


@router.get("/notifications", response_model=PlatformNotificationList)
async def platform_notifications(
    db: DB,
    user: Annotated[CurrentUser, Depends(require_platform_member)],
    limit: int = Query(30, ge=1, le=100),
    unread_only: bool = Query(False),
):
    await _read_scope(db)
    rows, is_read, unread = await _notification_rows(db, user, limit)
    items = []
    for log, school_name in rows:
        t = _ntype(log.action)
        read = is_read(log)
        if unread_only and read:
            continue
        body = log.message or log.action
        if school_name and school_name not in body:
            body = f"{school_name}: {body}"
        items.append(
            PlatformNotificationOut(
                id=log.id, title=_TITLES.get(t, "Activity"), body=body[:400], category=t,
                link_path=_link(t, log), is_read=read, created_at=log.created_at,
            )
        )
    return PlatformNotificationList(items=items, unread_count=unread)


@router.get("/notifications/unread-count")
async def platform_unread_count(db: DB, user: Annotated[CurrentUser, Depends(require_platform_member)]):
    await _read_scope(db)
    _, _, unread = await _notification_rows(db, user, 1)
    return {"count": unread}


async def _ensure_state(db: AsyncSession, user_id: UUID) -> PlatformNotificationRead:
    state = await _state(db, user_id)
    if state is None:
        state = PlatformNotificationRead(user_id=user_id, read_ids=[], prefs={})
        db.add(state)
        await db.flush()
    return state


@router.post("/notifications/{notification_id}/read")
async def platform_mark_read(
    notification_id: UUID, db: DB, user: Annotated[CurrentUser, Depends(require_platform_member)]
):
    state = await _ensure_state(db, user.id)
    ids = [x for x in (state.read_ids or []) if x != str(notification_id)]
    state.read_ids = (ids + [str(notification_id)])[-500:]
    await db.flush()
    return {"ok": True}


@router.post("/notifications/read-all")
async def platform_mark_all_read(db: DB, user: Annotated[CurrentUser, Depends(require_platform_member)]):
    state = await _ensure_state(db, user.id)
    state.read_all_at = _now()
    state.read_ids = []
    await db.flush()
    return {"count": 0}


@router.get("/notifications/preferences", response_model=NotificationPrefsOut)
async def platform_notification_prefs(db: DB, user: Annotated[CurrentUser, Depends(require_platform_member)]):
    state = await _state(db, user.id)
    prefs = (state.prefs if state else None) or {}
    return NotificationPrefsOut(
        types=[{"key": k, "label": m["label"], "enabled": bool(prefs.get(k, True))} for k, m in NOTIFY_TYPES.items()]
    )


@router.put("/notifications/preferences", response_model=NotificationPrefsOut)
async def platform_set_notification_prefs(
    body: NotificationPrefsIn, db: DB, user: Annotated[CurrentUser, Depends(require_platform_member)]
):
    state = await _ensure_state(db, user.id)
    prefs = dict(state.prefs or {})
    for k, v in body.enabled.items():
        if k in NOTIFY_TYPES:
            prefs[k] = bool(v)
    state.prefs = prefs
    await db.flush()
    return NotificationPrefsOut(
        types=[{"key": k, "label": m["label"], "enabled": bool(prefs.get(k, True))} for k, m in NOTIFY_TYPES.items()]
    )


# ── school members (ownership) ───────────────────────────────


class SchoolMemberOut(BaseModel):
    user_id: UUID
    membership_id: UUID | None = None
    full_name: str | None = None
    email: str
    role: str
    status: str
    is_owner: bool = False
    source: str  # "school" | "platform"
    last_login_at: datetime | None = None
    joined_at: datetime | None = None


@router.get("/schools/{school_id}/members", response_model=list[SchoolMemberOut])
async def school_members(
    school_id: UUID,
    db: DB,
    _: Annotated[CurrentUser, Depends(require_platform_member)],
    role: str | None = Query(None, max_length=32),
):
    """Everyone linked to the school: school admins first (the first active one is the owner), then
    other staff, teachers, students and parents, then Sybeez team members granted this school."""
    await _read_scope(db)
    school = await db.get(School, school_id)
    if school is None:
        raise NotFoundError("School not found")
    stmt = (
        select(SchoolMembership, User)
        .join(User, User.id == SchoolMembership.user_id)
        .where(SchoolMembership.school_id == school_id)
        .order_by(SchoolMembership.created_at.asc())
    )
    if role:
        stmt = stmt.where(SchoolMembership.role == role.upper())
    rows = (await db.execute(stmt)).all()
    order = {"SCHOOL_ADMIN": 0, "HR": 1, "HR_MANAGER": 1, "PAYROLL_ADMIN": 1, "PAYROLL_APPROVER": 1,
             "ACCOUNTANT": 1, "CASHIER": 1, "AUDITOR": 1, "FINANCE": 1, "STAFF": 2, "TEACHER": 3,
             "PARENT": 4, "STUDENT": 5}
    out: list[SchoolMemberOut] = []
    owner_set = False
    for mem, user in rows:
        owner = False
        if not owner_set and mem.role == "SCHOOL_ADMIN" and mem.status == "ACTIVE":
            owner = owner_set = True
        out.append(
            SchoolMemberOut(
                user_id=user.id, membership_id=mem.id, full_name=user.full_name, email=user.email,
                role=mem.role, status=mem.status, is_owner=owner, source="school",
                last_login_at=user.last_login_at, joined_at=mem.created_at,
            )
        )
    out.sort(key=lambda m: (not m.is_owner, order.get(m.role, 9), (m.full_name or m.email).lower()))
    if not role:
        granted = (
            await db.execute(
                select(PlatformSchoolAccess, User)
                .join(User, User.id == PlatformSchoolAccess.user_id)
                .where(PlatformSchoolAccess.school_id == school_id)
            )
        ).all()
        for acc, user in granted:
            out.append(
                SchoolMemberOut(
                    user_id=user.id, full_name=user.full_name, email=user.email,
                    role=f"SYBEEZ_{user.platform_role or 'TEAM'}", status="ACTIVE" if user.is_active else "INACTIVE",
                    source="platform", last_login_at=user.last_login_at, joined_at=acc.created_at,
                )
            )
        if school.created_by_id and all(m.user_id != school.created_by_id for m in out):
            creator = await db.get(User, school.created_by_id)
            if creator is not None:
                out.append(
                    SchoolMemberOut(
                        user_id=creator.id, full_name=creator.full_name, email=creator.email,
                        role="SYBEEZ_CREATOR", status="ACTIVE" if creator.is_active else "INACTIVE",
                        source="platform", last_login_at=creator.last_login_at, joined_at=school.created_at,
                    )
                )
    return out


# ── trials and the pay prompt ────────────────────────────────


class TrialOut(BaseModel):
    school_id: UUID
    trial_started_at: datetime
    trial_ends_at: datetime
    trial_days: int
    days_left: int
    status: Literal["trial", "payment_due", "active"]
    plan_code: str | None = None
    subscription_status: str | None = None


class TrialIn(BaseModel):
    trial_days: int | None = Field(default=None, ge=1, le=3650, description="Total trial length from the start date")
    extend_days: int | None = Field(default=None, ge=1, le=3650, description="Add days to the current end")
    mark_paid: bool | None = Field(default=None, description="Record that the school has paid (no gateway yet)")


async def _default_trial_days(db: AsyncSession) -> int:
    from src.db.models import PlatformSettings

    row = (await db.execute(select(PlatformSettings).limit(1))).scalar_one_or_none()
    return int(row.default_trial_days) if row and row.default_trial_days else 30


async def _latest_sub(db: AsyncSession, school_id: UUID) -> Subscription | None:
    return (
        await db.execute(
            select(Subscription)
            .where(Subscription.school_id == school_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def trial_for(db: AsyncSession, school: School) -> TrialOut:
    sub = await _latest_sub(db, school.id)
    start = school.created_at
    if sub is not None and sub.status == SubscriptionStatus.TRIAL.value and sub.starts_at:
        start = min(start, sub.starts_at) if start else sub.starts_at
    if sub is not None and sub.status == SubscriptionStatus.TRIAL.value and sub.ends_at:
        ends = sub.ends_at
    else:
        ends = start + timedelta(days=await _default_trial_days(db))
    now = _now()
    paid = sub is not None and sub.status == SubscriptionStatus.ACTIVE.value
    status: Literal["trial", "payment_due", "active"] = "active" if paid else ("trial" if now < ends else "payment_due")
    left = math.ceil((ends - now).total_seconds() / 86400) if ends > now else 0
    return TrialOut(
        school_id=school.id, trial_started_at=start, trial_ends_at=ends,
        trial_days=max(1, round((ends - start).total_seconds() / 86400)), days_left=left, status=status,
        plan_code=sub.plan_code if sub else None, subscription_status=sub.status if sub else None,
    )


@router.get("/schools/{school_id}/trial", response_model=TrialOut)
async def get_school_trial(school_id: UUID, db: DB, _: Annotated[CurrentUser, Depends(require_platform_member)]):
    await _read_scope(db)
    school = await db.get(School, school_id)
    if school is None:
        raise NotFoundError("School not found")
    return await trial_for(db, school)


@router.put("/schools/{school_id}/trial", response_model=TrialOut)
async def set_school_trial(
    school_id: UUID, body: TrialIn, db: DB, actor: Annotated[CurrentUser, Depends(require_super_admin)]
):
    """Set or extend a school's free trial (e.g. grant another 30 days), or record that it has paid."""
    from src.modules.audit.service import AuditService  # noqa: F401
    from src.modules.audit.repository import AuditRepository
    from src.modules.fees.tenant import bind_tenant

    school = await db.get(School, school_id)
    if school is None:
        raise NotFoundError("School not found")
    await bind_tenant(db, school.id)
    before = await trial_for(db, school)
    sub = await _latest_sub(db, school.id)
    if body.mark_paid:
        if sub is None:
            sub = Subscription(school_id=school.id, plan_code="standard", status=SubscriptionStatus.ACTIVE.value,
                               starts_at=_now())
            db.add(sub)
        else:
            sub.status = SubscriptionStatus.ACTIVE.value
    elif body.trial_days or body.extend_days:
        if sub is not None and sub.status not in {SubscriptionStatus.TRIAL.value, SubscriptionStatus.PAST_DUE.value}:
            raise ConflictError("This school is on a paid subscription; there is no trial to change.")
        if sub is None:
            sub = Subscription(school_id=school.id, plan_code="standard", status=SubscriptionStatus.TRIAL.value,
                               starts_at=before.trial_started_at)
            db.add(sub)
        sub.status = SubscriptionStatus.TRIAL.value
        if body.trial_days:
            sub.ends_at = before.trial_started_at + timedelta(days=body.trial_days)
        else:
            base = max(before.trial_ends_at, _now()) if before.status == "payment_due" else before.trial_ends_at
            sub.ends_at = base + timedelta(days=int(body.extend_days or 0))
    else:
        raise ConflictError("Send trial_days, extend_days or mark_paid.")
    await db.flush()
    after = await trial_for(db, school)
    await AuditRepository(db).write(
        action="platform.trial.update" if not body.mark_paid else "platform.subscription.paid",
        actor_id=actor.id,
        school_id=school.id,
        resource_type="school",
        resource_id=str(school.id),
        message=(
            f"Marked {school.name} as paid" if body.mark_paid
            else f"Trial for {school.name} now ends {after.trial_ends_at.date().isoformat()}"
        ),
        details={"before": before.trial_ends_at.isoformat(), "after": after.trial_ends_at.isoformat(),
                 "trial_days": body.trial_days, "extend_days": body.extend_days, "mark_paid": body.mark_paid},
    )
    return after


@router.get("/trials", response_model=list[TrialOut])
async def list_trials(db: DB, _: Annotated[CurrentUser, Depends(require_platform_member)]):
    await _read_scope(db)
    schools = (await db.execute(select(School).order_by(School.created_at.desc()))).scalars().all()
    return [await trial_for(db, s) for s in schools]


class BillingStatusOut(BaseModel):
    status: Literal["trial", "payment_due", "active", "none"]
    trial_ends_at: datetime | None = None
    days_left: int = 0
    message: str | None = None


@billing_router.get("/status", response_model=BillingStatusOut)
async def my_billing_status(db: DB, user: Annotated[CurrentUser, Depends(get_current_user)]):
    """Trial state for the signed-in user's school. Drives the pay prompt; never blocks access."""
    if user.school_id is None:
        return BillingStatusOut(status="none")
    school = await db.get(School, user.school_id)
    if school is None:
        return BillingStatusOut(status="none")
    t = await trial_for(db, school)
    msg = None
    if t.status == "payment_due":
        msg = "Your free trial has ended. Please contact Sybeez to activate your subscription."
    elif t.status == "trial":
        msg = f"Free trial: {t.days_left} day{'s' if t.days_left != 1 else ''} left."
    return BillingStatusOut(status=t.status, trial_ends_at=t.trial_ends_at, days_left=t.days_left, message=msg)
