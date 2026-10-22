"""Server-side payroll permissions.

A school admin always has every payroll ability. Other people receive a
membership role or a school grant. Auditors are read-only. An employee with
no payroll grant can only touch their own self-service records, and the
service filters those rows to that employee.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError
from src.db.models import PayrollRoleGrant
from src.shared.enums import UserRole

PAYROLL_ROLES = {
    UserRole.PAYROLL_ADMIN.value,
    UserRole.HR_MANAGER.value,
    UserRole.ACCOUNTANT.value,
    UserRole.PAYROLL_APPROVER.value,
    UserRole.AUDITOR.value,
}

SETTINGS_WRITE = {UserRole.SCHOOL_ADMIN.value, UserRole.PLATFORM_ADMIN.value, UserRole.PAYROLL_ADMIN.value, UserRole.HR_MANAGER.value}
STRUCTURE_WRITE = SETTINGS_WRITE
PREPARE = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.PAYROLL_ADMIN.value,
    UserRole.HR_MANAGER.value,
    UserRole.ACCOUNTANT.value,
}
APPROVE = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.PAYROLL_ADMIN.value,
    UserRole.PAYROLL_APPROVER.value,
}
READ = PREPARE | APPROVE | {
    UserRole.HR_MANAGER.value,
    UserRole.AUDITOR.value,
}
ATTENDANCE_ADMIN = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.HR.value,
    UserRole.HR_MANAGER.value,
    UserRole.PAYROLL_ADMIN.value,
}
GRANT_WRITE = {UserRole.SCHOOL_ADMIN.value, UserRole.PLATFORM_ADMIN.value, UserRole.PAYROLL_ADMIN.value}
AUDIT_READ = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.PAYROLL_ADMIN.value,
    UserRole.ACCOUNTANT.value,
    UserRole.AUDITOR.value,
}
PORTAL_ONLY_ROLES = {UserRole.ACCOUNTANT.value, UserRole.HR_MANAGER.value}
# Approver-only users do not see the payroll dashboard.
DASHBOARD = {
    UserRole.SCHOOL_ADMIN.value,
    UserRole.PLATFORM_ADMIN.value,
    UserRole.PAYROLL_ADMIN.value,
    UserRole.HR_MANAGER.value,
    UserRole.ACCOUNTANT.value,
    UserRole.AUDITOR.value,
}


async def grants_for(db: AsyncSession, school_id: UUID, user_id: UUID) -> set[str]:
    today = date.today()
    rows = (
        await db.execute(
            select(PayrollRoleGrant.role).where(
                PayrollRoleGrant.school_id == school_id,
                PayrollRoleGrant.user_id == user_id,
                PayrollRoleGrant.effective_from <= today,
                or_(PayrollRoleGrant.effective_to.is_(None), PayrollRoleGrant.effective_to >= today),
            )
        )
    ).scalars().all()
    return set(rows)


def _held(user: CurrentUser, extra: set[str]) -> set[str]:
    held = set(extra)
    if user.role == UserRole.PLATFORM_ADMIN or user.is_platform_admin:
        held.add(UserRole.PLATFORM_ADMIN.value)
    held.add(user.role.value)
    return held


async def require_payroll(db: AsyncSession, user: CurrentUser, allowed: set[str]) -> set[str]:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    from src.modules.access.catalog import permissions_for
    from src.modules.access.deps import effective_permissions

    extra = await grants_for(db, user.school_id, user.id)
    held = _held(user, extra)
    perms = await effective_permissions(db, user)
    defaults = set(permissions_for(user.role))
    role = user.role.value
    approve_revoked = "payroll.approve" in defaults and "payroll.approve" not in perms
    read_revoked = "payroll.read" in defaults and "payroll.read" not in perms and "payroll.approve" not in perms
    if approve_revoked and role in APPROVE:
        held.discard(role)
        if "payroll.read" in perms and role in PREPARE:
            held.add(UserRole.ACCOUNTANT.value)
        elif "payroll.read" in perms:
            held.add(UserRole.AUDITOR.value)
    if read_revoked and role in READ:
        held.discard(role)
    if "payroll.read" in perms and "payroll.read" not in defaults:
        held.add(UserRole.AUDITOR.value)
    if "payroll.approve" in perms and "payroll.approve" not in defaults:
        held.add(UserRole.PAYROLL_APPROVER.value)
    # Finance (Accountant) and HR Manager are portal roles, not payroll roles: they read or run payroll only
    # through an explicit payroll grant. HR Managers keep the payroll attendance actions.
    if role in PORTAL_ONLY_ROLES and role not in extra and not (role == UserRole.HR_MANAGER.value and allowed == ATTENDANCE_ADMIN):
        held.discard(role)
    held |= extra
    if held.isdisjoint(allowed):
        raise ForbiddenError("You do not have access to this payroll action")
    return held


def is_auditor_only(held: set[str]) -> bool:
    write_roles = held - {UserRole.AUDITOR.value}
    return UserRole.AUDITOR.value in held and not (write_roles & (READ - {UserRole.AUDITOR.value}))


# What the portal may offer. The server still checks every action.
PORTAL_PERMISSIONS: dict[str, set[str]] = {
    "employees.write": SETTINGS_WRITE,
    "structures.write": STRUCTURE_WRITE,
    "settings.write": SETTINGS_WRITE,
    "statutory.write": SETTINGS_WRITE,
    "runs.prepare": PREPARE,
    "runs.lock": PREPARE,
    "adjustments.write": PREPARE,
    "payments.write": PREPARE,
    "runs.approve": APPROVE,
    "runs.publish": APPROVE,
    "attendance.write": ATTENDANCE_ADMIN,
    "attendance.lock": ATTENDANCE_ADMIN,
    "permissions.write": GRANT_WRITE,
    "reports.view": READ,
    "audit.view": AUDIT_READ,
}

_ROLE_ORDER = (
    ("payroll_admin", {UserRole.SCHOOL_ADMIN.value, UserRole.PLATFORM_ADMIN.value, UserRole.PAYROLL_ADMIN.value}),
    ("hr_manager", {UserRole.HR_MANAGER.value}),
    ("accountant", {UserRole.ACCOUNTANT.value}),
    ("payroll_approver", {UserRole.PAYROLL_APPROVER.value}),
    ("auditor", {UserRole.AUDITOR.value}),
)
_ROLE_LABELS = {
    "payroll_admin": "Payroll admin",
    "hr_manager": "HR manager",
    "accountant": "Accountant",
    "payroll_approver": "Payroll approver",
    "auditor": "Auditor",
    "employee": "Employee",
}


def portal_access(held: set[str]) -> dict:
    role = next((name for name, roles in _ROLE_ORDER if held & roles), "employee")
    permissions = sorted(code for code, roles in PORTAL_PERMISSIONS.items() if held & roles)
    if is_auditor_only(held):
        permissions = [code for code in permissions if code in {"reports.view", "audit.view"}]
    return {"role": role, "role_label": _ROLE_LABELS[role], "permissions": permissions}


async def payroll_access(db: AsyncSession, user: CurrentUser) -> dict:
    """Effective payroll role for the portal, including school grants and permission overrides."""
    try:
        held = await require_payroll(db, user, {role.value for role in UserRole})
    except ForbiddenError:
        held = set()
    return portal_access(held)
