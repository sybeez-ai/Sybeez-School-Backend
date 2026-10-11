"""Super-admin assignment of internal staff portals and feature permissions."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.core.security import hash_password
from src.db.models import (
    PlatformStaff,
    PlatformStaffInvitation,
    PlatformStaffPermission,
    PlatformStaffPortal,
    User,
)
from src.modules.access.audit import write_audit
from src.modules.access.catalog import PERMISSIONS, PORTALS
from src.modules.auth.repository import hash_token
from src.shared.enums import InvitationStatus, UserRole
from src.shared.utils import generate_token, utcnow


def _check_portals(portals: list[str]) -> list[str]:
    cleaned = []
    for portal in portals:
        name = portal.strip().lower().replace("-", "_")
        if name == "school-admin":
            name = "school_admin"
        if name not in PORTALS:
            raise ValidationError(f"Unknown portal: {portal}")
        if name not in cleaned:
            cleaned.append(name)
    return cleaned


def _check_features(permissions: list[str]) -> list[str]:
    cleaned = []
    for code in permissions:
        if code == "platform.manage":
            raise ForbiddenError("Platform APIs are limited to the super admin")
        if code not in PERMISSIONS:
            raise ValidationError(f"Unknown permission: {code}")
        if code not in cleaned:
            cleaned.append(code)
    return cleaned


class StaffService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _user_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def _staff(self, staff_id: UUID) -> PlatformStaff:
        row = (
            await self.db.execute(select(PlatformStaff).where(PlatformStaff.id == staff_id))
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("Staff member not found")
        return row

    async def create(
        self,
        actor: CurrentUser,
        *,
        email: str,
        full_name: str,
        password: str,
        portals: list[str],
        permissions: list[str],
    ) -> PlatformStaff:
        email = email.lower().strip()
        if email == get_settings().superadmin_email.lower():
            raise ForbiddenError("The super admin account cannot be assigned as staff")
        portals = _check_portals(portals)
        permissions = _check_features(permissions)
        user = await self._user_by_email(email)
        if user is None:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(password),
                is_platform_admin=False,
                is_active=True,
                status="ACTIVE",
            )
            self.db.add(user)
            await self.db.flush()
        elif user.is_platform_admin:
            raise ForbiddenError("The super admin account cannot be assigned as staff")
        existing = (
            await self.db.execute(select(PlatformStaff).where(PlatformStaff.user_id == user.id))
        ).scalar_one_or_none()
        if existing and existing.status == "ACTIVE":
            raise ConflictError("This person is already platform staff")
        if existing:
            existing.status = "ACTIVE"
            existing.invited_by = actor.id
            staff = existing
        else:
            staff = PlatformStaff(user_id=user.id, status="ACTIVE", invited_by=actor.id)
            self.db.add(staff)
            await self.db.flush()
        await self._replace_access(actor, staff, portals, permissions, action="staff.grant")
        return staff

    async def set_access(
        self,
        actor: CurrentUser,
        staff_id: UUID,
        *,
        portals: list[str],
        permissions: list[str],
    ) -> PlatformStaff:
        staff = await self._staff(staff_id)
        if staff.status != "ACTIVE":
            raise ConflictError("Staff access is revoked")
        await self._replace_access(
            actor,
            staff,
            _check_portals(portals),
            _check_features(permissions),
            action="staff.grant",
        )
        return staff

    async def _replace_access(
        self,
        actor: CurrentUser,
        staff: PlatformStaff,
        portals: list[str],
        permissions: list[str],
        *,
        action: str,
    ) -> None:
        current_portals = {
            row.portal
            for row in (
                await self.db.execute(select(PlatformStaffPortal).where(PlatformStaffPortal.staff_id == staff.id))
            ).scalars()
        }
        current_perms = {
            row.permission_code
            for row in (
                await self.db.execute(
                    select(PlatformStaffPermission).where(PlatformStaffPermission.staff_id == staff.id)
                )
            ).scalars()
        }
        for portal in current_portals - set(portals):
            row = (
                await self.db.execute(
                    select(PlatformStaffPortal).where(
                        PlatformStaffPortal.staff_id == staff.id, PlatformStaffPortal.portal == portal
                    )
                )
            ).scalar_one()
            await self.db.delete(row)
            await self._audit(actor, staff, "staff.revoke", f"Revoked portal {portal}", {"portal": portal})
        for portal in portals:
            if portal not in current_portals:
                self.db.add(PlatformStaffPortal(staff_id=staff.id, portal=portal))
        for code in current_perms - set(permissions):
            row = (
                await self.db.execute(
                    select(PlatformStaffPermission).where(
                        PlatformStaffPermission.staff_id == staff.id,
                        PlatformStaffPermission.permission_code == code,
                    )
                )
            ).scalar_one()
            await self.db.delete(row)
            await self._audit(actor, staff, "staff.revoke", f"Revoked {code}", {"permission": code})
        for code in permissions:
            if code not in current_perms:
                self.db.add(
                    PlatformStaffPermission(staff_id=staff.id, permission_code=code, granted_by=actor.id)
                )
                await self._audit(actor, staff, "staff.permission.grant", f"Granted {code}", {"permission": code})
        await self.db.flush()
        await self._audit(
            actor,
            staff,
            action,
            "Updated staff access",
            {"portals": portals, "permissions": permissions},
        )

    async def revoke(self, actor: CurrentUser, staff_id: UUID) -> PlatformStaff:
        staff = await self._staff(staff_id)
        staff.status = "REVOKED"
        await self.db.flush()
        await self._audit(actor, staff, "staff.revoke", "Revoked staff access", {"status": "REVOKED"})
        return staff

    async def list_staff(self) -> list[dict]:
        rows = (await self.db.execute(select(PlatformStaff).order_by(PlatformStaff.created_at.desc()))).scalars().all()
        out = []
        for staff in rows:
            user = (await self.db.execute(select(User).where(User.id == staff.user_id))).scalar_one()
            portals, permissions = await self._access(staff.id)
            out.append(
                {
                    "id": staff.id,
                    "user_id": staff.user_id,
                    "email": user.email,
                    "full_name": user.full_name,
                    "status": staff.status,
                    "portals": portals,
                    "permissions": permissions,
                }
            )
        return out

    async def _access(self, staff_id: UUID) -> tuple[list[str], list[str]]:
        portals = [
            row.portal
            for row in (
                await self.db.execute(select(PlatformStaffPortal).where(PlatformStaffPortal.staff_id == staff_id))
            ).scalars()
        ]
        permissions = [
            row.permission_code
            for row in (
                await self.db.execute(
                    select(PlatformStaffPermission).where(PlatformStaffPermission.staff_id == staff_id)
                )
            ).scalars()
        ]
        return sorted(portals), sorted(permissions)

    async def invite(
        self,
        actor: CurrentUser,
        *,
        email: str,
        portals: list[str],
        permissions: list[str],
        expires_in_days: int = 7,
    ) -> tuple[PlatformStaffInvitation, str]:
        email = email.lower().strip()
        if email == get_settings().superadmin_email.lower():
            raise ForbiddenError("The super admin account cannot be invited as staff")
        portals = _check_portals(portals)
        permissions = _check_features(permissions)
        raw = generate_token(24)
        inv = PlatformStaffInvitation(
            email=email,
            token_hash=hash_token(raw),
            portals=portals,
            permissions=permissions,
            status=InvitationStatus.PENDING.value,
            invited_by=actor.id,
            expires_at=utcnow() + timedelta(days=expires_in_days),
        )
        self.db.add(inv)
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=None,
            actor_id=actor.id,
            action="staff.invite",
            resource_type="platform_staff_invitation",
            resource_id=str(inv.id),
            message=f"Invited {email} as platform staff",
            details={"email": email, "portals": portals, "permissions": permissions},
        )
        return inv, raw

    async def accept(self, *, token: str, full_name: str, password: str) -> tuple[User, PlatformStaff]:
        inv = (
            await self.db.execute(
                select(PlatformStaffInvitation).where(PlatformStaffInvitation.token_hash == hash_token(token))
            )
        ).scalar_one_or_none()
        if inv is None:
            raise NotFoundError("Invitation not found")
        if inv.status != InvitationStatus.PENDING.value:
            raise ConflictError("Invitation already used or revoked")
        if inv.expires_at < utcnow():
            inv.status = InvitationStatus.EXPIRED.value
            raise ForbiddenError("Invitation expired")
        user = await self._user_by_email(inv.email)
        if user is None:
            user = User(
                email=inv.email,
                full_name=full_name,
                password_hash=hash_password(password),
                is_platform_admin=False,
                is_active=True,
                status="ACTIVE",
            )
            self.db.add(user)
            await self.db.flush()
        else:
            if user.password_hash is None:
                user.password_hash = hash_password(password)
            user.full_name = full_name
        staff = (
            await self.db.execute(select(PlatformStaff).where(PlatformStaff.user_id == user.id))
        ).scalar_one_or_none()
        if staff is None:
            staff = PlatformStaff(user_id=user.id, status="ACTIVE", invited_by=inv.invited_by)
            self.db.add(staff)
            await self.db.flush()
        else:
            staff.status = "ACTIVE"
        actor = CurrentUser(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=UserRole.PLATFORM_STAFF,
            school_id=None,
            membership_id=None,
            is_platform_admin=False,
        )
        await self._replace_access(actor, staff, list(inv.portals), list(inv.permissions), action="staff.grant")
        inv.status = InvitationStatus.ACCEPTED.value
        inv.accepted_at = utcnow()
        await self.db.flush()
        return user, staff

    async def _audit(self, actor: CurrentUser, staff: PlatformStaff, action: str, message: str, details: dict) -> None:
        await write_audit(
            self.db,
            school_id=None,
            actor_id=actor.id,
            action=action,
            resource_type="platform_staff",
            resource_id=str(staff.id),
            message=message,
            details={"user_id": str(staff.user_id), **details},
        )
