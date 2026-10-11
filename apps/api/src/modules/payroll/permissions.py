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
    extra = await grants_for(db, user.school_id, user.id)
    held = _held(user, extra)
    if held.isdisjoint(allowed):
        raise ForbiddenError("You do not have access to this payroll action")
    return held


def is_auditor_only(held: set[str]) -> bool:
    write_roles = held - {UserRole.AUDITOR.value}
    return UserRole.AUDITOR.value in held and not (write_roles & (READ - {UserRole.AUDITOR.value}))
