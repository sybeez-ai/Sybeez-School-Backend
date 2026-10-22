"""Feature controls. Platform super admin: global and per-school rules. School admin: switches in their own school."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, get_current_user, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import (
    MembershipFeatureLimit,
    PlatformFeatureRule,
    School,
    SchoolFeatureSetting,
    SchoolMembership,
)
from src.modules.access.audit import write_audit
from src.modules.access.deps import require_super_admin
from src.modules.features import service
from src.modules.features.catalog import FEATURES, is_known_key, label_for
from src.shared.enums import UserRole

DB = Annotated[AsyncSession, Depends(get_db)]

me_router = APIRouter(prefix="/features", tags=["features"])
platform_router = APIRouter(prefix="/platform/features", tags=["platform-features"])


class FeatureState(BaseModel):
    key: str
    label: str
    portal: str
    enabled: bool
    locked_by: str | None = None
    reason: str | None = None
    school_enabled: bool = True  # the school admin's own switch, whatever the platform says


class RuleIn(BaseModel):
    enabled: bool
    reason: str | None = Field(default=None, max_length=300)


class SchoolSwitchIn(BaseModel):
    enabled: bool


def _check(key: str) -> None:
    if not is_known_key(key):
        raise NotFoundError("Unknown feature")


def _school(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("No school selected")
    return user.school_id


async def _states(
    db: AsyncSession, school_id: UUID, role: str, *, detail: bool, membership_id: UUID | None = None
) -> list[FeatureState]:
    amap = await service.effective_map(db, school_id, role, membership_id)
    own: set[str] = set()
    if detail:
        rows = (
            await db.execute(
                select(SchoolFeatureSetting).where(
                    SchoolFeatureSetting.school_id == school_id, SchoolFeatureSetting.enabled.is_(False)
                )
            )
        ).scalars().all()
        own = {r.feature_key for r in rows}
    out = []
    for key, a in amap.items():
        meta = FEATURES.get(key) or {"label": label_for(key), "portal": key.split(".")[1] if key.startswith("nav.") else "all"}
        out.append(
            FeatureState(
                key=key,
                label=meta["label"],
                portal=meta["portal"],
                enabled=a.enabled,
                locked_by=a.locked_by,
                # Only school admins are told why; everyone else just sees the feature gone.
                reason=a.reason if detail and a.locked_by == "platform" else None,
                school_enabled=key not in own,
            )
        )
    return out


@me_router.get("/me", response_model=list[FeatureState])
async def my_features(db: DB, user: Annotated[CurrentUser, Depends(get_current_user)]):
    """What this signed-in user's school has switched on (the web nav and the AI assistant read this)."""
    if user.school_id is None:
        return [
            FeatureState(key=k, label=m["label"], portal=m["portal"], enabled=True) for k, m in FEATURES.items()
        ]
    return await _states(
        db, user.school_id, user.role.value, detail=user.role == UserRole.SCHOOL_ADMIN, membership_id=user.membership_id
    )


@me_router.get("/school", response_model=list[FeatureState])
async def school_features(db: DB, user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]):
    return await _states(db, _school(user), UserRole.SCHOOL_ADMIN.value, detail=True)


@me_router.put("/school/{key}", response_model=list[FeatureState])
async def set_school_feature(
    key: str,
    body: SchoolSwitchIn,
    db: DB,
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    """Turn a feature off (or back on) for this school only. Cannot touch anything the platform has blocked."""
    _check(key)
    sid = _school(user)
    a = (await service.effective_map(db, sid, UserRole.SCHOOL_ADMIN.value)).get(key, service.Access(True))
    if a.locked_by == "platform":
        raise ForbiddenError("This feature is locked by Sybeez and cannot be changed by the school.", code="feature_locked")
    if key == "portal.school" and not body.enabled:
        raise ValidationError("You cannot turn off the school admin portal itself.")
    row = (
        await db.execute(
            select(SchoolFeatureSetting).where(
                SchoolFeatureSetting.school_id == sid, SchoolFeatureSetting.feature_key == key
            )
        )
    ).scalar_one_or_none()
    before = True if row is None else row.enabled
    if row is None:
        db.add(SchoolFeatureSetting(school_id=sid, feature_key=key, enabled=body.enabled, updated_by=user.id))
    else:
        row.enabled = body.enabled
        row.updated_by = user.id
    await write_audit(
        db,
        school_id=sid,
        actor_id=user.id,
        action="feature.school_switch",
        resource_type="feature",
        resource_id=key,
        message=f"School admin turned {label_for(key)} {'on' if body.enabled else 'off'}",
        details={"feature": key, "before": before, "after": body.enabled},
    )
    await db.flush()
    service.invalidate(sid)
    return await _states(db, sid, UserRole.SCHOOL_ADMIN.value, detail=True)


class RuleOut(BaseModel):
    feature_key: str
    school_id: UUID | None
    enabled: bool
    reason: str | None


@platform_router.get("", response_model=list[RuleOut])
async def list_rules(db: DB, _: Annotated[CurrentUser, Depends(require_super_admin())]):
    rows = (await db.execute(select(PlatformFeatureRule))).scalars().all()
    return [RuleOut(feature_key=r.feature_key, school_id=r.school_id, enabled=r.enabled, reason=r.reason) for r in rows]


@platform_router.get("/catalog")
async def catalog(_: Annotated[CurrentUser, Depends(require_super_admin())]):
    return [{"key": k, "label": m["label"], "portal": m["portal"]} for k, m in FEATURES.items()]


async def _put_rule(db: AsyncSession, user: CurrentUser, key: str, school_id: UUID | None, body: RuleIn) -> RuleOut:
    _check(key)
    if school_id is not None and await db.get(School, school_id) is None:
        raise NotFoundError("School not found")
    q = select(PlatformFeatureRule).where(PlatformFeatureRule.feature_key == key)
    q = q.where(PlatformFeatureRule.school_id.is_(None) if school_id is None else PlatformFeatureRule.school_id == school_id)
    row = (await db.execute(q)).scalar_one_or_none()
    before = True if row is None else row.enabled
    if body.enabled and row is not None:
        await db.execute(delete(PlatformFeatureRule).where(PlatformFeatureRule.id == row.id))  # no rule = enabled
    elif not body.enabled:
        if row is None:
            db.add(PlatformFeatureRule(feature_key=key, school_id=school_id, enabled=False, reason=body.reason, updated_by=user.id))
        else:
            row.enabled, row.reason, row.updated_by = False, body.reason, user.id
    scope = "all schools" if school_id is None else f"school {school_id}"
    await write_audit(
        db,
        school_id=None,  # platform audit row (RLS admits platform rows without a school); target is in details
        actor_id=user.id,
        action="feature.platform_rule",
        resource_type="feature",
        resource_id=key,
        message=f"Platform {'enabled' if body.enabled else 'blocked'} {label_for(key)} for {scope}",
        details={"feature": key, "school_id": None if school_id is None else str(school_id),
                 "scope": "global" if school_id is None else "school", "before": before,
                 "after": body.enabled, "reason": body.reason},
    )
    await db.flush()
    service.invalidate(school_id)
    if school_id is None:
        service.invalidate()
    return RuleOut(feature_key=key, school_id=school_id, enabled=body.enabled, reason=body.reason)


@platform_router.put("/global/{key}", response_model=RuleOut)
async def put_global(key: str, body: RuleIn, db: DB, user: Annotated[CurrentUser, Depends(require_super_admin())]):
    return await _put_rule(db, user, key, None, body)


@platform_router.put("/schools/{school_id}/{key}", response_model=RuleOut)
async def put_school_rule(
    school_id: UUID, key: str, body: RuleIn, db: DB, user: Annotated[CurrentUser, Depends(require_super_admin())]
):
    return await _put_rule(db, user, key, school_id, body)


@platform_router.get("/schools/{school_id}", response_model=list[FeatureState])
async def school_view(school_id: UUID, db: DB, _: Annotated[CurrentUser, Depends(require_super_admin())]):
    """Effective state for a school as its admin would see it (platform locks plus the school's own switches)."""
    if await db.get(School, school_id) is None:
        raise NotFoundError("School not found")
    return await _states(db, school_id, UserRole.SCHOOL_ADMIN.value, detail=True)


class MemberFeaturesOut(BaseModel):
    membership_id: UUID
    role: str
    off: list[str]


@me_router.get("/members/{membership_id}", response_model=MemberFeaturesOut)
async def member_features(
    membership_id: UUID, db: DB, user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
):
    m = await _member(db, user, membership_id)
    return MemberFeaturesOut(membership_id=m.id, role=m.role, off=sorted(await _off(db, m.id)))


async def _off(db: AsyncSession, membership_id: UUID) -> set[str]:
    rows = (
        await db.execute(
            select(MembershipFeatureLimit.feature_key).where(MembershipFeatureLimit.membership_id == membership_id)
        )
    ).scalars().all()
    return set(rows)


async def _member(db: AsyncSession, user: CurrentUser, membership_id: UUID) -> SchoolMembership:
    m = (
        await db.execute(
            select(SchoolMembership).where(
                SchoolMembership.id == membership_id, SchoolMembership.school_id == _school(user)
            )
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFoundError("Membership not found")
    if m.user_id == user.id or m.role in (UserRole.SCHOOL_ADMIN.value, UserRole.PLATFORM_ADMIN.value):
        raise ForbiddenError("School admins' own access is managed by Sybeez")
    return m


@me_router.put("/members/{membership_id}/{key}", response_model=MemberFeaturesOut)
async def set_member_feature(
    membership_id: UUID,
    key: str,
    body: SchoolSwitchIn,
    db: DB,
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    """Turn one feature off (or back on) for one staff member. Only narrows what the platform and school allow."""
    _check(key)
    m = await _member(db, user, membership_id)
    row = (
        await db.execute(
            select(MembershipFeatureLimit).where(
                MembershipFeatureLimit.membership_id == m.id, MembershipFeatureLimit.feature_key == key
            )
        )
    ).scalar_one_or_none()
    if body.enabled and row is not None:
        await db.execute(delete(MembershipFeatureLimit).where(MembershipFeatureLimit.id == row.id))
    elif not body.enabled and row is None:
        db.add(
            MembershipFeatureLimit(
                school_id=_school(user), membership_id=m.id, feature_key=key, updated_by=user.id
            )
        )
    await write_audit(
        db,
        school_id=_school(user),
        actor_id=user.id,
        action="feature.member_switch",
        resource_type="membership",
        resource_id=str(m.id),
        message=f"School admin turned {label_for(key)} {'on' if body.enabled else 'off'} for a {m.role} member",
        details={"feature": key, "membership_id": str(m.id), "role": m.role, "after": body.enabled},
    )
    await db.flush()
    service.invalidate_member(m.id)
    return MemberFeaturesOut(membership_id=m.id, role=m.role, off=sorted(await _off(db, m.id)))
