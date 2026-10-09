"""Platform SaaS plans, settings, and AI control-plane helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import PlatformAiSettings, PlatformAiUsageDaily, PlatformPlan, PlatformSettings, Subscription
from src.modules.audit.repository import AuditRepository
from src.modules.platform.schemas import (
    PlanCreate,
    PlanOut,
    PlanUpdate,
    PlatformAiOverview,
    PlatformAiSettingsOut,
    PlatformAiSettingsUpdate,
    PlatformSettingsOut,
    PlatformSettingsUpdate,
    MetricPoint,
)
from src.shared.enums import SubscriptionStatus


class PlatformSaasService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit = AuditRepository(db)

    async def list_plans(self, *, include_archived: bool = False) -> list[PlanOut]:
        q = select(PlatformPlan).order_by(PlatformPlan.sort_order.asc(), PlatformPlan.name.asc())
        if not include_archived:
            q = q.where(PlatformPlan.is_active.is_(True))
        plans = list((await self.db.execute(q)).scalars().all())
        outs: list[PlanOut] = []
        for p in plans:
            active = (
                await self.db.execute(
                    select(func.count())
                    .select_from(Subscription)
                    .where(
                        Subscription.plan_code == p.code,
                        Subscription.status.in_(
                            [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIAL.value]
                        ),
                    )
                )
            ).scalar_one()
            outs.append(self._plan_out(p, active_schools=active))
        return outs

    async def get_plan(self, plan_id: UUID) -> PlanOut:
        plan = await self.db.get(PlatformPlan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")
        active = (
            await self.db.execute(
                select(func.count()).select_from(Subscription).where(Subscription.plan_code == plan.code)
            )
        ).scalar_one()
        return self._plan_out(plan, active_schools=active)

    async def create_plan(self, actor: CurrentUser, data: PlanCreate) -> PlanOut:
        existing = (
            await self.db.execute(select(PlatformPlan).where(PlatformPlan.code == data.code.lower()))
        ).scalar_one_or_none()
        if existing:
            raise ConflictError("Plan code already exists")
        plan = PlatformPlan(
            id=uuid4(),
            code=data.code.lower(),
            name=data.name,
            description=data.description,
            price_monthly=data.price_monthly,
            currency=data.currency,
            billing_cycle=data.billing_cycle,
            max_students=data.max_students,
            max_teachers=data.max_teachers,
            max_storage_gb=data.max_storage_gb,
            ai_monthly_tokens=data.ai_monthly_tokens,
            entitlements=data.entitlements or {},
            is_public=data.is_public,
            sort_order=data.sort_order,
        )
        self.db.add(plan)
        await self.db.flush()
        await self.audit.write(
            action="platform.plan.create",
            actor_id=actor.id,
            resource_type="platform_plan",
            resource_id=str(plan.id),
            message=f"Plan created: {plan.code}",
        )
        return self._plan_out(plan)

    async def update_plan(self, actor: CurrentUser, plan_id: UUID, data: PlanUpdate) -> PlanOut:
        plan = await self.db.get(PlatformPlan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(plan, k, v)
        await self.db.flush()
        await self.audit.write(
            action="platform.plan.update",
            actor_id=actor.id,
            resource_type="platform_plan",
            resource_id=str(plan.id),
            message=f"Plan updated: {plan.code}",
            details={"fields": list(data.model_dump(exclude_unset=True).keys())},
        )
        return self._plan_out(plan)

    async def archive_plan(self, actor: CurrentUser, plan_id: UUID) -> PlanOut:
        plan = await self.db.get(PlatformPlan, plan_id)
        if not plan:
            raise NotFoundError("Plan not found")
        plan.is_active = False
        await self.db.flush()
        await self.audit.write(
            action="platform.plan.archive",
            actor_id=actor.id,
            resource_type="platform_plan",
            resource_id=str(plan.id),
            message=f"Plan archived: {plan.code}",
        )
        return self._plan_out(plan)

    def _plan_out(self, plan: PlatformPlan, *, active_schools: int = 0) -> PlanOut:
        return PlanOut(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            description=plan.description,
            price_monthly=float(plan.price_monthly or 0),
            currency=plan.currency,
            billing_cycle=plan.billing_cycle,
            max_students=plan.max_students,
            max_teachers=plan.max_teachers,
            max_storage_gb=plan.max_storage_gb,
            ai_monthly_tokens=plan.ai_monthly_tokens,
            entitlements=plan.entitlements or {},
            is_active=plan.is_active,
            is_public=plan.is_public,
            sort_order=plan.sort_order,
            active_schools=active_schools,
            created_at=plan.created_at,
        )

    async def get_settings(self) -> PlatformSettingsOut:
        row = await self._ensure_settings()
        return PlatformSettingsOut.model_validate(row)

    async def update_settings(self, actor: CurrentUser, data: PlatformSettingsUpdate) -> PlatformSettingsOut:
        row = await self._ensure_settings()
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        await self.db.flush()
        await self.audit.write(
            action="platform.settings.update",
            actor_id=actor.id,
            resource_type="platform_settings",
            resource_id=str(row.id),
            message="Platform settings updated",
            details={"fields": list(data.model_dump(exclude_unset=True).keys())},
        )
        return PlatformSettingsOut.model_validate(row)

    async def _ensure_settings(self) -> PlatformSettings:
        row = (await self.db.execute(select(PlatformSettings).limit(1))).scalar_one_or_none()
        if row:
            return row
        row = PlatformSettings(id=uuid4())
        self.db.add(row)
        await self.db.flush()
        return row

    async def get_ai_settings(self) -> PlatformAiSettingsOut:
        row = await self._ensure_ai_settings()
        return PlatformAiSettingsOut.model_validate(row)

    async def update_ai_settings(
        self, actor: CurrentUser, data: PlatformAiSettingsUpdate
    ) -> PlatformAiSettingsOut:
        row = await self._ensure_ai_settings()
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(row, k, v)
        await self.db.flush()
        await self.audit.write(
            action="platform.ai.settings.update",
            actor_id=actor.id,
            resource_type="platform_ai_settings",
            resource_id=str(row.id),
            message="AI settings updated",
            details={"fields": list(data.model_dump(exclude_unset=True).keys())},
        )
        return PlatformAiSettingsOut.model_validate(row)

    async def _ensure_ai_settings(self) -> PlatformAiSettings:
        row = (await self.db.execute(select(PlatformAiSettings).limit(1))).scalar_one_or_none()
        if row:
            return row
        row = PlatformAiSettings(
            id=uuid4(),
            providers_json={
                "qwen": {"enabled": True, "status": "configured"},
                "deepseek": {"enabled": False, "status": "optional"},
                "openai": {"enabled": False, "status": "optional"},
            },
            models_json=[
                {"provider": "qwen", "model": "qwen3.8-flash", "capability": "CHAT", "default": True},
                {"provider": "qwen", "model": "text-embedding-v3", "capability": "EMBEDDING", "default": True},
                {"provider": "qwen", "model": "qwen-vl-plus", "capability": "VISION", "default": True},
            ],
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def ai_overview(self) -> PlatformAiOverview:
        since = datetime.now(timezone.utc) - timedelta(days=30)
        rows = list(
            (
                await self.db.execute(
                    select(PlatformAiUsageDaily).where(PlatformAiUsageDaily.usage_date >= since)
                )
            ).scalars().all()
        )
        requests = sum(r.requests for r in rows)
        tokens = sum(r.input_tokens + r.output_tokens for r in rows)
        cost = float(sum(float(r.estimated_cost or 0) for r in rows))
        errors = sum(r.errors for r in rows)
        safety = sum(r.safety_blocks for r in rows)
        schools = {r.school_id for r in rows if r.school_id}

        by_provider: dict[str, int] = {}
        by_model: dict[str, int] = {}
        by_day: dict[str, int] = {}
        for r in rows:
            by_provider[r.provider] = by_provider.get(r.provider, 0) + r.requests
            by_model[r.model] = by_model.get(r.model, 0) + r.requests
            day = r.usage_date.date().isoformat()
            by_day[day] = by_day.get(day, 0) + r.requests

        return PlatformAiOverview(
            ai_requests=requests,
            ai_tokens=tokens,
            ai_estimated_cost=round(cost, 4),
            active_ai_schools=len(schools),
            safety_blocks=safety,
            provider_errors=errors,
            by_provider=[MetricPoint(label=k, value=v) for k, v in sorted(by_provider.items())],
            by_model=[MetricPoint(label=k, value=v) for k, v in sorted(by_model.items())],
            trend=[MetricPoint(label=k, value=v) for k, v in sorted(by_day.items())],
        )

    async def entitlements_for_plan_code(self, plan_code: str | None) -> dict:
        if not plan_code:
            return {}
        plan = (
            await self.db.execute(select(PlatformPlan).where(PlatformPlan.code == plan_code.lower()))
        ).scalar_one_or_none()
        return dict(plan.entitlements or {}) if plan else {}
