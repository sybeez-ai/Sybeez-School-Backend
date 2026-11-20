"""Role -> portal enforcement at login, including platform staff portal grants."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api.dependencies import CurrentUser, require_platform_member
from src.core.exceptions import ForbiddenError
from src.modules.auth.service import AuthService
from src.shared.enums import MembershipStatus, UserRole


def _service(*, staff_portals=None, membership_role=None):
    svc = AuthService.__new__(AuthService)
    svc.db = MagicMock()
    svc.repo = MagicMock()
    svc._staff_portals_without_membership = AsyncMock(return_value=staff_portals)
    membership = (
        SimpleNamespace(role=membership_role.value, status=MembershipStatus.ACTIVE.value)
        if membership_role
        else None
    )

    async def by_role(_uid, role, _school):
        return membership if membership and membership.role == role else None

    svc.repo.get_membership_for_role = AsyncMock(side_effect=by_role)
    svc.repo.get_membership = AsyncMock(return_value=membership)
    return svc


def _user(platform_admin=False):
    return SimpleNamespace(id=uuid4(), is_platform_admin=platform_admin)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,portal",
    [
        (UserRole.TEACHER, "teacher"),
        (UserRole.SCHOOL_ADMIN, "school"),
        (UserRole.STUDENT, "student"),
        (UserRole.PARENT, "parent"),
        (UserRole.ACCOUNTANT, "finance"),
    ],
)
async def test_role_signs_in_on_own_portal(role, portal):
    await _service(membership_role=role)._assert_portal_role(_user(), portal)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,portal",
    [
        (UserRole.TEACHER, "school"),
        (UserRole.TEACHER, "platform"),
        (UserRole.TEACHER, "student"),
        (UserRole.SCHOOL_ADMIN, "teacher"),
        (UserRole.SCHOOL_ADMIN, "platform"),
        (UserRole.STUDENT, "parent"),
        (UserRole.PARENT, "teacher"),
    ],
)
async def test_role_rejected_on_other_portal(role, portal):
    with pytest.raises(ForbiddenError):
        await _service(membership_role=role)._assert_portal_role(_user(), portal)


@pytest.mark.asyncio
async def test_platform_admin_rejected_on_school_portal():
    with pytest.raises(ForbiddenError):
        await _service()._assert_portal_role(_user(platform_admin=True), "teacher")


@pytest.mark.asyncio
async def test_staff_signs_in_only_on_granted_portals():
    svc = _service(staff_portals={"platform", "finance"})
    await svc._assert_portal_role(_user(), "platform")
    await svc._assert_portal_role(_user(), "finance")
    for portal in ("payroll", "hrm", "teacher", "school"):
        with pytest.raises(ForbiddenError):
            await svc._assert_portal_role(_user(), portal)


def _cu(role, *, platform_admin=False, portals=()):
    return CurrentUser(
        id=uuid4(),
        email="t@sybeez.ai",
        full_name="T",
        role=role,
        school_id=None,
        membership_id=None,
        is_platform_admin=platform_admin,
        portals=portals,
    )


@pytest.mark.asyncio
async def test_platform_member_gate():
    assert await require_platform_member(_cu(UserRole.PLATFORM_ADMIN, platform_admin=True))
    assert await require_platform_member(_cu(UserRole.PLATFORM_STAFF, portals=("platform",)))
    for user in (
        _cu(UserRole.PLATFORM_STAFF, portals=("finance",)),
        _cu(UserRole.TEACHER),
        _cu(UserRole.SCHOOL_ADMIN),
    ):
        with pytest.raises(ForbiddenError):
            await require_platform_member(user)
