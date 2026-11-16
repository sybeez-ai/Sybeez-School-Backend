"""One platform employee's profile, sign-ins, and audit trail."""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import NotFoundError
from src.db.models import AuditLog, PlatformStaff, School, User

LOGIN_ACTIONS = ("auth.login", "auth.login_failed")

ACTION_GROUPS: dict[str, tuple[str, ...]] = {
    "sign-in": ("auth.login",),
    "failed-sign-in": ("auth.login_failed",),
    "schools": ("school.",),
    "admins": ("admin.", "school.admin", "membership."),
    "plans": ("plan.",),
    "features": ("feature.",),
    "ai": ("ai.", "platform.ai_"),
    "employees": ("staff.", "employee.", "team."),
    "reports": ("report.", "export."),
}


def action_matches(action: str, selected: str | None) -> bool:
    """True when an audit action belongs to the selected group or exact action."""
    if not selected:
        return True
    group = ACTION_GROUPS.get(selected)
    if group is None:
        return action == selected
    for item in group:
        if item.endswith(".") or item.endswith("_"):
            if action.startswith(item):
                return True
        elif action == item:
            return True
    return False


def target_href(resource_type: str | None, resource_id: str | None) -> str | None:
    if not resource_id:
        return None
    kind = (resource_type or "").lower()
    if kind in {"school", "schools"}:
        return f"/platform/schools/{resource_id}"
    if kind in {"plan", "plans"}:
        return "/platform/plans"
    if kind in {"user", "employee", "staff"}:
        return f"/platform/staff/{resource_id}"
    return None


def _day_start(value: date) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)


async def resolve_employee(db: AsyncSession, key: UUID) -> tuple[User, PlatformStaff | None]:
    """A staff row id, or the user id used for a console-only employee."""
    staff = await db.get(PlatformStaff, key)
    if staff is not None:
        user = await db.get(User, staff.user_id)
        if user is None:
            raise NotFoundError("Employee not found")
        return user, staff
    user = await db.get(User, key)
    if user is None:
        raise NotFoundError("Employee not found")
    staff = (
        await db.execute(select(PlatformStaff).where(PlatformStaff.user_id == user.id))
    ).scalar_one_or_none()
    if staff is None and not user.is_platform_admin:
        raise NotFoundError("Employee not found")
    return user, staff


async def employee_profile(db: AsyncSession, key: UUID) -> dict:
    user, staff = await resolve_employee(db, key)
    added_by = None
    if staff is not None and staff.invited_by is not None:
        inviter = await db.get(User, staff.invited_by)
        added_by = inviter.full_name if inviter else None
    status = staff.status if staff is not None else ("ACTIVE" if user.is_active else "REVOKED")
    return {
        "id": staff.id if staff is not None else user.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "role": user.platform_role,
        "status": status,
        "added_at": (staff.created_at if staff is not None else user.created_at),
        "added_by": added_by,
        "last_login_at": user.last_login_at,
    }


def _who(user_id: UUID, email: str):
    return or_(
        AuditLog.actor_id == user_id,
        and_(
            AuditLog.actor_id.is_(None),
            func.lower(func.coalesce(AuditLog.details["email"].astext, "")) == email.lower(),
        ),
    )


async def employee_activity(
    db: AsyncSession,
    key: UUID,
    *,
    kind: str = "all",
    action: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    await db.execute(text("SELECT set_config('app.rls_bypass', 'on', true)"))
    user, _staff = await resolve_employee(db, key)
    conds = [_who(user.id, user.email)]
    if kind == "sign-ins":
        conds.append(AuditLog.action.in_(LOGIN_ACTIONS))
    elif kind == "activity":
        conds.append(AuditLog.action.notin_(LOGIN_ACTIONS))
    if date_from is not None:
        conds.append(AuditLog.created_at >= _day_start(date_from))
    if date_to is not None:
        conds.append(AuditLog.created_at < _day_start(date_to) + timedelta(days=1))
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        conds.append(
            or_(
                func.lower(AuditLog.action).like(like),
                func.lower(func.coalesce(AuditLog.message, "")).like(like),
                func.lower(func.coalesce(AuditLog.resource_id, "")).like(like),
            )
        )
    rows = (
        await db.execute(
            select(AuditLog, School.name)
            .outerjoin(School, School.id == AuditLog.school_id)
            .where(*conds)
            .order_by(desc(AuditLog.created_at))
        )
    ).all()
    items = []
    for log, school_name in rows:
        if not action_matches(log.action, action):
            continue
        details = log.details or None
        items.append(
            {
                "id": log.id,
                "created_at": log.created_at,
                "action": log.action,
                "message": log.message,
                "school_id": log.school_id,
                "school_name": school_name,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "target_href": target_href(log.resource_type, log.resource_id)
                or (f"/platform/schools/{log.school_id}" if log.school_id else None),
                "target_label": school_name or log.resource_id,
                "ip_address": log.ip_address,
                "user_agent": (details or {}).get("user_agent") if isinstance(details, dict) else None,
                "via": (details or {}).get("via") if isinstance(details, dict) else None,
                "details": details,
            }
        )
    total = len(items)
    start = (page - 1) * page_size
    return {"items": items[start : start + page_size], "total": total, "page": page, "page_size": page_size}


def activity_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["created_at", "action", "message", "target", "ip_address", "via"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "created_at": row["created_at"].isoformat() if row.get("created_at") else "",
                "action": row.get("action") or "",
                "message": row.get("message") or "",
                "target": row.get("target_label") or "",
                "ip_address": row.get("ip_address") or "",
                "via": row.get("via") or "",
            }
        )
    return buffer.getvalue()
