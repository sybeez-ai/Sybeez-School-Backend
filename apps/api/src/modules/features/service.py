"""Effective feature access: platform (global AND per school) AND school admin switch."""

import time
from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import MembershipFeatureLimit, PlatformFeatureRule, SchoolFeatureSetting
from src.modules.features.catalog import FEATURES, PORTAL_FOR_ROLE

TTL_SECONDS = 30.0
# school_id -> (loaded_at, global_off, school_off_by_platform, school_off_by_admin); reasons kept for the UI.
_CACHE: dict[UUID, tuple[float, dict[str, str | None], dict[str, str | None], set[str]]] = {}


@dataclass(frozen=True)
class Access:
    enabled: bool
    locked_by: str | None = None  # "platform" | "school"
    reason: str | None = None
    key: str | None = None


_MCACHE: dict[UUID, tuple[float, set[str]]] = {}


async def member_off(db: AsyncSession, membership_id: UUID | None) -> set[str]:
    """Features a school admin switched off for this one staff member."""
    if membership_id is None:
        return set()
    hit = _MCACHE.get(membership_id)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1]
    rows = (
        await db.execute(
            select(MembershipFeatureLimit.feature_key).where(MembershipFeatureLimit.membership_id == membership_id)
        )
    ).scalars().all()
    out = set(rows)
    _MCACHE[membership_id] = (time.monotonic(), out)
    return out


def invalidate_member(membership_id: UUID) -> None:
    _MCACHE.pop(membership_id, None)


def invalidate(school_id: UUID | None = None) -> None:
    """Drop cached rules. Called on every write in this process; other tasks catch up within TTL_SECONDS."""
    if school_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(school_id, None)


async def _load(db: AsyncSession, school_id: UUID):
    hit = _CACHE.get(school_id)
    if hit and time.monotonic() - hit[0] < TTL_SECONDS:
        return hit[1], hit[2], hit[3]
    rules = (
        await db.execute(
            select(PlatformFeatureRule).where(
                (PlatformFeatureRule.school_id.is_(None)) | (PlatformFeatureRule.school_id == school_id)
            )
        )
    ).scalars().all()
    glob = {r.feature_key: r.reason for r in rules if r.school_id is None and not r.enabled}
    per_school = {r.feature_key: r.reason for r in rules if r.school_id == school_id and not r.enabled}
    mine = (
        await db.execute(select(SchoolFeatureSetting).where(SchoolFeatureSetting.school_id == school_id))
    ).scalars().all()
    off = {r.feature_key for r in mine if not r.enabled}
    _CACHE[school_id] = (time.monotonic(), glob, per_school, off)
    return glob, per_school, off


def decide(key: str, glob, per_school, school_off) -> Access:
    """Platform wins: a platform block cannot be lifted by the school."""
    if key in per_school:
        return Access(False, "platform", per_school[key])
    if key in glob:
        return Access(False, "platform", glob[key])
    if key in school_off:
        return Access(False, "school", None)
    return Access(True)


async def access_for(
    db: AsyncSession, school_id: UUID, keys: list[str], membership_id: UUID | None = None
) -> Access:
    """First restriction found over ``keys`` (feature and/or portal), platform before school."""
    glob, per_school, school_off = await _load(db, school_id)
    found: Access | None = None
    for k in keys:
        a = decide(k, glob, per_school, school_off)
        if not a.enabled:
            a = replace(a, key=k)
            if a.locked_by == "platform":
                return a
            found = found or a
    if found is None:
        mine = await member_off(db, membership_id)
        for k in keys:
            if k in mine:
                return Access(False, "staff", None, k)
    return found or Access(True)


async def effective_map(
    db: AsyncSession, school_id: UUID, role: str, membership_id: UUID | None = None
) -> dict[str, Access]:
    glob, per_school, school_off = await _load(db, school_id)
    school_off = set(school_off) | await member_off(db, membership_id)
    portal = PORTAL_FOR_ROLE.get(role)
    portal_access = decide(portal, glob, per_school, school_off) if portal else Access(True)
    out: dict[str, Access] = {}
    for key in FEATURES:
        a = decide(key, glob, per_school, school_off)
        if a.enabled and not portal_access.enabled and not key.startswith("portal."):
            a = portal_access
        out[key] = a
    return out
