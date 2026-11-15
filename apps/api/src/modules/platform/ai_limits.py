"""Monthly token caps and the model a school or person should use.

A blank person or group value inherits the next wider setting. A reached cap
stops the next AI call. The call that crossed the cap is still counted.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import NotFoundError, ValidationError
from src.db.models import (
    PlatformAiLimit,
    PlatformAiSettings,
    PlatformAiUsageDaily,
    PlatformAiUserMonth,
    School,
    SchoolMembership,
    Student,
    Teacher,
    User,
)
from src.modules.audit.repository import AuditRepository

SCHOOL_STOP = "This school's monthly AI token limit has been reached. A platform admin can raise it."
PERSON_STOP = "Your monthly AI token limit has been reached. A platform admin can raise it."
GROUPS = ("students", "staff", "parents")
SCOPES = ("school", *GROUPS, "user")


class LimitIn(BaseModel):
    monthly_token_limit: int | None = Field(default=None, ge=0, le=2_000_000_000)
    model: str | None = Field(default=None, max_length=120)


def group_for_role(role: str | None) -> str:
    value = (role or "").strip().upper()
    if value == "STUDENT":
        return "students"
    if value == "PARENT":
        return "parents"
    return "staff"


def month_start(now: datetime | None = None) -> date:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date().replace(day=1)


def _text(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned[:120] or None


def _first(rows: list[dict | None], field: str):
    for row in rows:
        if row is None:
            continue
        value = row.get(field)
        if field == "model":
            value = _text(value)
        if value is not None:
            return value
    return None


def effective_assignment(
    school: dict | None,
    group: dict | None,
    person: dict | None,
) -> dict:
    """Person overrides the role group, which overrides the school."""
    return {
        "monthly_token_limit": _first([person, group, school], "monthly_token_limit"),
        "model": _first([person, group, school], "model"),
    }


def allowance_for(
    *,
    school_used: int,
    school_limit: int | None,
    person_used: int,
    person_limit: int | None,
    model: str | None,
) -> dict:
    if school_limit is not None and school_used >= school_limit:
        return {
            "allowed": False,
            "message": SCHOOL_STOP,
            "model": model,
            "used": school_used,
            "limit": school_limit,
            "scope": "school",
        }
    if person_limit is not None and person_used >= person_limit:
        return {
            "allowed": False,
            "message": PERSON_STOP,
            "model": model,
            "used": person_used,
            "limit": person_limit,
            "scope": "person",
        }
    used = person_used if person_limit is not None else school_used
    limit = person_limit if person_limit is not None else school_limit
    return {
        "allowed": True,
        "message": None,
        "model": model,
        "used": used,
        "limit": limit,
        "scope": "person" if person_limit is not None else "school",
    }


def _row_dict(row: PlatformAiLimit | None) -> dict | None:
    if row is None:
        return None
    return {"monthly_token_limit": row.monthly_token_limit, "model": row.model}


async def _limits(db: AsyncSession, school_id: UUID) -> list[PlatformAiLimit]:
    return list(
        (
            await db.execute(select(PlatformAiLimit).where(PlatformAiLimit.school_id == school_id))
        ).scalars()
    )


def _index(rows: list[PlatformAiLimit]) -> dict:
    school = None
    groups: dict[str, PlatformAiLimit] = {}
    people: dict[UUID, PlatformAiLimit] = {}
    for row in rows:
        if row.scope == "school":
            school = row
        elif row.scope in GROUPS and row.user_id is None:
            groups[row.scope] = row
        elif row.user_id is not None:
            people[row.user_id] = row
    return {"school": school, "groups": groups, "people": people}


async def school_month_tokens(db: AsyncSession, school_id: UUID, start: date | None = None) -> int:
    start = start or month_start()
    stamp = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    value = (
        await db.execute(
            select(
                func.coalesce(
                    func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens),
                    0,
                )
            ).where(
                PlatformAiUsageDaily.school_id == school_id,
                PlatformAiUsageDaily.usage_date >= stamp,
            )
        )
    ).scalar_one()
    return int(value or 0)


async def person_month_tokens(db: AsyncSession, school_id: UUID, start: date | None = None) -> dict[UUID, int]:
    start = start or month_start()
    rows = (
        await db.execute(
            select(PlatformAiUserMonth.user_id, PlatformAiUserMonth.tokens).where(
                PlatformAiUserMonth.school_id == school_id,
                PlatformAiUserMonth.month_start == start,
            )
        )
    ).all()
    return {user_id: int(tokens or 0) for user_id, tokens in rows}


async def model_choices(db: AsyncSession) -> list[str]:
    row = (await db.execute(select(PlatformAiSettings).limit(1))).scalar_one_or_none()
    found: list[str] = []
    for item in (row.models_json if row is not None else None) or []:
        if isinstance(item, dict):
            name = _text(str(item.get("model") or ""))
        else:
            name = _text(str(item))
        if name and name not in found:
            found.append(name)
    return found


async def annotate_schools(db: AsyncSession, rows: list[dict]) -> dict:
    """Add this month's usage, the school cap, and the assigned model. No prices."""
    start = month_start()
    choices = await model_choices(db)
    limits = list((await db.execute(select(PlatformAiLimit).where(PlatformAiLimit.scope == "school"))).scalars())
    by_school = {row.school_id: row for row in limits}
    stamped = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    usage = (
        await db.execute(
            select(
                PlatformAiUsageDaily.school_id,
                func.coalesce(
                    func.sum(PlatformAiUsageDaily.input_tokens + PlatformAiUsageDaily.output_tokens),
                    0,
                ),
            )
            .where(PlatformAiUsageDaily.usage_date >= stamped)
            .group_by(PlatformAiUsageDaily.school_id)
        )
    ).all()
    month = {school_id: int(tokens or 0) for school_id, tokens in usage}
    items = []
    for row in rows:
        school_id = row.get("school_id")
        policy = by_school.get(school_id)
        items.append(
            {
                **row,
                "month_tokens": month.get(school_id, 0),
                "monthly_token_limit": None if policy is None else policy.monthly_token_limit,
                "model": None if policy is None else policy.model,
            }
        )
    return {"items": items, "model_choices": choices}


async def annotate_people(db: AsyncSession, detail: dict) -> dict:
    school_id = detail["school_id"]
    indexed = _index(await _limits(db, school_id))
    school = _row_dict(indexed["school"])
    groups = {name: _row_dict(indexed["groups"].get(name)) for name in GROUPS}
    months = await person_month_tokens(db, school_id)
    school_month = await school_month_tokens(db, school_id)

    def paint(person: dict) -> dict:
        own = _row_dict(indexed["people"].get(person.get("user_id")))
        group = groups.get(group_for_role(person.get("role")))
        effective = effective_assignment(school, group, own)
        person["month_tokens"] = months.get(person.get("user_id"), 0)
        person["monthly_token_limit"] = effective["monthly_token_limit"]
        person["model"] = effective["model"]
        person["limit_override"] = None if own is None else own["monthly_token_limit"]
        person["model_override"] = None if own is None else own["model"]
        return person

    for key in ("students", "teachers", "others"):
        detail[key] = [paint(person) for person in detail.get(key) or []]
    detail["month_tokens"] = school_month
    detail["monthly_token_limit"] = None if school is None else school["monthly_token_limit"]
    detail["model"] = None if school is None else school["model"]
    detail["groups"] = {
        name: {
            "monthly_token_limit": None if groups[name] is None else groups[name]["monthly_token_limit"],
            "model": None if groups[name] is None else groups[name]["model"],
        }
        for name in GROUPS
    }
    detail["model_choices"] = await model_choices(db)
    return detail


async def add_person_month(db: AsyncSession, *, school_id: UUID, user_id: UUID, tokens: int, when: datetime | None = None) -> None:
    if tokens <= 0:
        return
    start = month_start(when)
    stmt = pg_insert(PlatformAiUserMonth).values(
        school_id=school_id,
        user_id=user_id,
        month_start=start,
        tokens=tokens,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ai_user_month",
        set_={
            "tokens": PlatformAiUserMonth.tokens + stmt.excluded.tokens,
            "updated_at": func.now(),
        },
    )
    await db.execute(stmt)


async def _school(db: AsyncSession, school_id: UUID) -> School:
    school = await db.get(School, school_id)
    if school is None:
        raise NotFoundError("School not found")
    return school


async def _upsert_scope(
    db: AsyncSession,
    school_id: UUID,
    scope: str,
    user_id: UUID | None,
    body: LimitIn,
) -> PlatformAiLimit:
    if scope not in SCOPES:
        raise ValidationError("Unknown limit scope")
    if scope == "user" and user_id is None:
        raise ValidationError("A person is required")
    if scope != "user":
        user_id = None
    rows = await _limits(db, school_id)
    current = None
    for row in rows:
        if scope == "user" and row.user_id == user_id:
            current = row
            break
        if scope != "user" and row.scope == scope and row.user_id is None:
            current = row
            break
    if current is None:
        current = PlatformAiLimit(school_id=school_id, scope=scope, user_id=user_id)
        db.add(current)
    fields = body.model_fields_set
    if "monthly_token_limit" in fields:
        current.monthly_token_limit = body.monthly_token_limit
    if "model" in fields:
        current.model = _text(body.model)
    await db.flush()
    return current


async def _audit(db: AsyncSession, actor: CurrentUser, school_id: UUID, scope: str, row: PlatformAiLimit) -> None:
    label = scope if row.user_id is None else f"user {row.user_id}"
    await AuditRepository(db).write(
        action="platform.ai.limit.update",
        actor_id=actor.id,
        school_id=school_id,
        resource_type="platform_ai_limit",
        resource_id=str(row.id),
        message=f"Updated the AI token limit for {label}",
        details={
            "scope": scope,
            "user_id": None if row.user_id is None else str(row.user_id),
            "monthly_token_limit": row.monthly_token_limit,
            "model": row.model,
        },
    )


async def save_school_limit(db: AsyncSession, actor: CurrentUser, school_id: UUID, body: LimitIn) -> dict:
    await _school(db, school_id)
    row = await _upsert_scope(db, school_id, "school", None, body)
    await _audit(db, actor, school_id, "school", row)
    return {"school_id": school_id, "monthly_token_limit": row.monthly_token_limit, "model": row.model}


async def save_group_limit(db: AsyncSession, actor: CurrentUser, school_id: UUID, group: str, body: LimitIn) -> dict:
    if group not in GROUPS:
        raise ValidationError("Group must be students, staff, or parents")
    await _school(db, school_id)
    row = await _upsert_scope(db, school_id, group, None, body)
    await _audit(db, actor, school_id, group, row)
    return {"school_id": school_id, "group": group, "monthly_token_limit": row.monthly_token_limit, "model": row.model}


async def _person_in_school(db: AsyncSession, school_id: UUID, user_id: UUID) -> str:
    student = (
        await db.execute(select(Student.id).where(Student.school_id == school_id, Student.user_id == user_id))
    ).first()
    if student is not None:
        return "STUDENT"
    teacher = (
        await db.execute(select(Teacher.id).where(Teacher.school_id == school_id, Teacher.user_id == user_id))
    ).first()
    if teacher is not None:
        return "TEACHER"
    member = (
        await db.execute(
            select(SchoolMembership.role).where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.user_id == user_id,
            )
        )
    ).first()
    if member is None:
        raise NotFoundError("Person not found at this school")
    return str(member[0])


async def save_person_limit(
    db: AsyncSession, actor: CurrentUser, school_id: UUID, user_id: UUID, body: LimitIn
) -> dict:
    await _school(db, school_id)
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("Person not found")
    await _person_in_school(db, school_id, user_id)
    row = await _upsert_scope(db, school_id, "user", user_id, body)
    await _audit(db, actor, school_id, "user", row)
    return {
        "school_id": school_id,
        "user_id": user_id,
        "monthly_token_limit": row.monthly_token_limit,
        "model": row.model,
    }


async def allowance_for_user(db: AsyncSession, actor: CurrentUser) -> dict:
    """Whether this signed-in person may spend more tokens, and which model to use."""
    if actor.school_id is None:
        return allowance_for(school_used=0, school_limit=None, person_used=0, person_limit=None, model=None)
    school_id = actor.school_id
    indexed = _index(await _limits(db, school_id))
    role = await _person_role(db, school_id, actor)
    group_name = group_for_role(role)
    school = _row_dict(indexed["school"])
    group = _row_dict(indexed["groups"].get(group_name))
    person = _row_dict(indexed["people"].get(actor.id))
    effective = effective_assignment(school, group, person)
    school_used = await school_month_tokens(db, school_id)
    person_used = (await person_month_tokens(db, school_id)).get(actor.id, 0)
    return allowance_for(
        school_used=school_used,
        school_limit=None if school is None else school["monthly_token_limit"],
        person_used=person_used,
        person_limit=_first([person, group], "monthly_token_limit"),
        model=effective["model"],
    )


async def _person_role(db: AsyncSession, school_id: UUID, actor: CurrentUser) -> str:
    try:
        return await _person_in_school(db, school_id, actor.id)
    except NotFoundError:
        return (actor.role.value if actor.role is not None else "") or ""
