"""Record AI token usage. Counts only — prices are never stored or returned here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, ValidationError
from src.db.models import PlatformAiUsageDaily, PlatformAiUserUsage
from src.modules.audit.repository import AuditRepository

USAGE_BATCH_LIMIT = 500


class AiUsageIn(BaseModel):
    school_id: UUID | None = None
    user_id: UUID | None = None
    role: str | None = Field(default=None, max_length=32)
    model: str = Field(default="unknown", max_length=120)
    provider: str = Field(default="unknown", max_length=64)
    capability: str = Field(default="CHAT", max_length=64)
    input_tokens: int = Field(default=0, ge=0, le=2_000_000_000)
    output_tokens: int = Field(default=0, ge=0, le=2_000_000_000)


class AiUsageBackfillIn(BaseModel):
    rows: list[AiUsageIn] = Field(default_factory=list, max_length=USAGE_BATCH_LIMIT)


@dataclass(frozen=True)
class UsageWrite:
    school_id: UUID | None
    user_id: UUID
    role: str | None
    model: str
    provider: str
    capability: str
    input_tokens: int
    output_tokens: int

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def usage_day(now: datetime | None = None) -> datetime:
    """Midnight UTC for the calendar day a usage row belongs to."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _clip(value: str | None, default: str, limit: int) -> str:
    text_value = (value or "").strip()
    if not text_value:
        text_value = default
    return text_value[:limit]


def normalize_usage(row: AiUsageIn, actor: CurrentUser) -> UsageWrite:
    """Use the posted counts. A missing school falls back to the caller's school; a missing user is the caller.

    Platform admins may attribute a row to another school or person (the backfill path).
    Everyone else can only record their own usage.
    """
    if not actor.is_platform_admin:
        if row.school_id is not None and row.school_id != actor.school_id:
            raise ForbiddenError("Cannot record usage for another school")
        if row.user_id is not None and row.user_id != actor.id:
            raise ForbiddenError("Cannot record usage for another user")
    role = (row.role or "").strip()[:32] or None
    return UsageWrite(
        school_id=row.school_id if row.school_id is not None else actor.school_id,
        user_id=row.user_id or actor.id,
        role=role,
        model=_clip(row.model, "unknown", 120),
        provider=_clip(row.provider, "unknown", 64),
        capability=_clip(row.capability, "CHAT", 64),
        input_tokens=int(row.input_tokens or 0),
        output_tokens=int(row.output_tokens or 0),
    )


async def apply_usage(db: AsyncSession, actor: CurrentUser, row: AiUsageIn, *, when: datetime | None = None) -> UsageWrite:
    """Add one event onto today's daily total and, when a school is known, onto that person's model total."""
    usage = normalize_usage(row, actor)
    day = usage_day(when)
    daily = pg_insert(PlatformAiUsageDaily).values(
        id=uuid4(),
        usage_date=day,
        school_id=usage.school_id,
        provider=usage.provider,
        model=usage.model,
        capability=usage.capability,
        role=usage.role,
        requests=1,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        estimated_cost=0,
        errors=0,
        safety_blocks=0,
    )
    added = {
        "requests": PlatformAiUsageDaily.requests + daily.excluded.requests,
        "input_tokens": PlatformAiUsageDaily.input_tokens + daily.excluded.input_tokens,
        "output_tokens": PlatformAiUsageDaily.output_tokens + daily.excluded.output_tokens,
        "updated_at": func.now(),
    }
    if usage.school_id is None:
        daily = daily.on_conflict_do_update(
            index_elements=["usage_date", "provider", "model", "capability"],
            index_where=text("school_id IS NULL"),
            set_=added,
        )
    else:
        daily = daily.on_conflict_do_update(constraint="uq_ai_usage_daily", set_=added)
    await db.execute(daily)

    if usage.school_id is not None:
        person = pg_insert(PlatformAiUserUsage).values(
            id=uuid4(),
            school_id=usage.school_id,
            user_id=usage.user_id,
            model=usage.model,
            tokens=usage.tokens,
        )
        person = person.on_conflict_do_update(
            constraint="uq_ai_user_usage_school_user_model",
            set_={
                "tokens": PlatformAiUserUsage.tokens + person.excluded.tokens,
                "updated_at": func.now(),
            },
        )
        await db.execute(person)
        from src.modules.platform.ai_limits import add_person_month

        await add_person_month(
            db,
            school_id=usage.school_id,
            user_id=usage.user_id,
            tokens=usage.tokens,
            when=day,
        )
    await db.flush()
    return usage


async def record_usage(db: AsyncSession, actor: CurrentUser, row: AiUsageIn) -> dict:
    """Live metering from the signed-in caller. No audit row — this is not a human delete."""
    from src.modules.fees.tenant import set_rls_bypass

    await set_rls_bypass(db, True)
    usage = await apply_usage(db, actor, row)
    return {"written": 1, "tokens": usage.tokens}


async def backfill_ai_usage(db: AsyncSession, actor: CurrentUser, rows: list[AiUsageIn]) -> dict:
    """Replay usage the AI service already stored. One audit row for the batch. Does not invent counts."""
    from src.modules.fees.tenant import set_rls_bypass

    if len(rows) > USAGE_BATCH_LIMIT:
        raise ValidationError(f"At most {USAGE_BATCH_LIMIT} usage rows can be replayed at once")
    await set_rls_bypass(db, True)
    for row in rows:
        await apply_usage(db, actor, row)
    await AuditRepository(db).write(
        action="platform.ai_usage.backfill",
        actor_id=actor.id,
        resource_type="ai_usage",
        resource_id=str(actor.id),
        message=f"Replayed {len(rows)} AI usage rows",
        details={"written": len(rows)},
    )
    return {"written": len(rows)}
