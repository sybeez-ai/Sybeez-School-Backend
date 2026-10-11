from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.db.models import AuditLog, Invitation, MembershipPermissionOverride, SchoolMembership, User
from src.modules.access.audit import write_audit
from src.modules.access.catalog import (
    ALL_PERMISSIONS,
    GRANT_EFFECTS,
    PERMISSIONS,
    ROLE_PERMISSIONS,
    SCHOOL_ASSIGNABLE_ROLES,
)
from src.modules.access.deps import effective_permissions
from src.modules.access.schemas import InvitationIn, PermissionChangeIn, RoleChangeIn, RoleGrantIn
from src.modules.auth.repository import hash_token
from src.modules.schools.provisioning import create_school_user
from src.shared.enums import InvitationStatus, MembershipStatus, UserRole
from src.shared.pagination import PageParams
from src.shared.utils import generate_token, utcnow


class AccessService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _assignable(self, actor: CurrentUser, role: UserRole) -> None:
        if role == UserRole.PLATFORM_ADMIN or role not in SCHOOL_ASSIGNABLE_ROLES:
            if actor.role != UserRole.PLATFORM_ADMIN:
                raise ForbiddenError("That role cannot be assigned by a school admin")
        if actor.role != UserRole.PLATFORM_ADMIN and role not in SCHOOL_ASSIGNABLE_ROLES:
            raise ForbiddenError("Role is not assignable in a school")

    async def _membership(self, school_id: UUID, membership_id: UUID) -> SchoolMembership:
        result = await self.db.execute(
            select(SchoolMembership).where(
                SchoolMembership.id == membership_id,
                SchoolMembership.school_id == school_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("Membership not found")
        return row

    def _reject_self(self, actor: CurrentUser, membership: SchoolMembership) -> None:
        if membership.user_id == actor.id:
            raise ForbiddenError("Another admin must change your own role or permissions")

    async def invite(self, actor: CurrentUser, school_id: UUID, data: InvitationIn) -> tuple[Invitation, str]:
        self._assignable(actor, data.role)
        raw = generate_token(24)
        inv = Invitation(
            school_id=school_id,
            email=data.email.lower(),
            role=data.role.value,
            token_hash=hash_token(raw),
            status=InvitationStatus.PENDING.value,
            invited_by=actor.id,
            expires_at=utcnow() + timedelta(days=data.expires_in_days),
            message=data.message,
        )
        self.db.add(inv)
        await self.db.flush()
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action="role.grant",
            resource_type="invitation",
            resource_id=str(inv.id),
            message=f"Invited {inv.email} as {inv.role}",
            details={"email": inv.email, "role": inv.role, "status": "pending"},
        )
        return inv, raw

    async def list_invitations(self, school_id: UUID, page: PageParams) -> tuple[list[Invitation], int]:
        base = select(Invitation).where(Invitation.school_id == school_id)
        total = (await self.db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
        result = await self.db.execute(
            base.order_by(Invitation.created_at.desc()).offset(page.offset).limit(page.page_size)
        )
        return list(result.scalars().all()), int(total)

    async def revoke_invitation(self, actor: CurrentUser, school_id: UUID, invitation_id: UUID) -> Invitation:
        result = await self.db.execute(
            select(Invitation).where(Invitation.id == invitation_id, Invitation.school_id == school_id)
        )
        inv = result.scalar_one_or_none()
        if inv is None:
            raise NotFoundError("Invitation not found")
        if inv.status != InvitationStatus.PENDING.value:
            raise ConflictError("Invitation is not pending")
        inv.status = InvitationStatus.REVOKED.value
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action="role.revoke",
            resource_type="invitation",
            resource_id=str(inv.id),
            message=f"Revoked invitation for {inv.email} ({inv.role})",
            details={"email": inv.email, "role": inv.role},
        )
        return inv

    async def grant_role(self, actor: CurrentUser, school_id: UUID, data: RoleGrantIn) -> SchoolMembership:
        self._assignable(actor, data.role)
        user, membership, _temp = await create_school_user(
            self.db,
            school_id=school_id,
            email=data.email,
            full_name=data.full_name,
            password=data.password,
            role=data.role,
            actor_id=actor.id,
        )
        if user.id == actor.id:
            raise ForbiddenError("Another admin must change your own role")
        return membership

    async def change_role(self, actor: CurrentUser, school_id: UUID, membership_id: UUID, data: RoleChangeIn) -> SchoolMembership:
        self._assignable(actor, data.role)
        membership = await self._membership(school_id, membership_id)
        self._reject_self(actor, membership)
        if membership.role == data.role.value and membership.status == MembershipStatus.ACTIVE.value:
            return membership
        old = membership.role
        conflict = await self.db.execute(
            select(SchoolMembership).where(
                SchoolMembership.user_id == membership.user_id,
                SchoolMembership.school_id == school_id,
                SchoolMembership.role == data.role.value,
                SchoolMembership.id != membership.id,
            )
        )
        if conflict.scalar_one_or_none() is not None:
            raise ConflictError("User already has that role in this school")
        membership.role = data.role.value
        membership.status = MembershipStatus.ACTIVE.value
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action="role.revoke",
            resource_type="membership",
            resource_id=str(membership.id),
            message=f"Revoked role {old}",
            details={"user_id": str(membership.user_id), "role": old},
        )
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action="role.grant",
            resource_type="membership",
            resource_id=str(membership.id),
            message=f"Granted role {data.role.value}",
            details={"user_id": str(membership.user_id), "role": data.role.value},
        )
        return membership

    async def revoke_membership(self, actor: CurrentUser, school_id: UUID, membership_id: UUID) -> SchoolMembership:
        membership = await self._membership(school_id, membership_id)
        self._reject_self(actor, membership)
        if membership.status == MembershipStatus.LEFT.value:
            return membership
        membership.status = MembershipStatus.LEFT.value
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action="role.revoke",
            resource_type="membership",
            resource_id=str(membership.id),
            message=f"Revoked role {membership.role}",
            details={"user_id": str(membership.user_id), "role": membership.role},
        )
        return membership

    async def set_override(
        self, actor: CurrentUser, school_id: UUID, membership_id: UUID, data: PermissionChangeIn
    ) -> MembershipPermissionOverride:
        if data.effect not in GRANT_EFFECTS:
            raise ValidationError("effect must be GRANT or REVOKE")
        if data.permission not in ALL_PERMISSIONS:
            raise ValidationError("Unknown permission")
        if data.permission == "platform.manage" and actor.role != UserRole.PLATFORM_ADMIN:
            raise ForbiddenError("platform.manage cannot be granted by a school admin")
        membership = await self._membership(school_id, membership_id)
        self._reject_self(actor, membership)
        result = await self.db.execute(
            select(MembershipPermissionOverride).where(
                MembershipPermissionOverride.membership_id == membership.id,
                MembershipPermissionOverride.permission_code == data.permission,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = MembershipPermissionOverride(
                school_id=school_id,
                membership_id=membership.id,
                permission_code=data.permission,
                effect=data.effect,
                granted_by=actor.id,
            )
            self.db.add(row)
        else:
            row.effect = data.effect
            row.granted_by = actor.id
        try:
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Permission override already exists") from exc
        action = "permission.grant" if data.effect == "GRANT" else "permission.revoke"
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action=action,
            resource_type="membership_permission",
            resource_id=str(row.id),
            message=f"{data.effect} {data.permission}",
            details={
                "membership_id": str(membership.id),
                "user_id": str(membership.user_id),
                "permission": data.permission,
                "effect": data.effect,
            },
        )
        return row

    async def clear_override(self, actor: CurrentUser, school_id: UUID, membership_id: UUID, permission: str) -> None:
        membership = await self._membership(school_id, membership_id)
        self._reject_self(actor, membership)
        result = await self.db.execute(
            select(MembershipPermissionOverride).where(
                MembershipPermissionOverride.membership_id == membership.id,
                MembershipPermissionOverride.permission_code == permission,
                MembershipPermissionOverride.school_id == school_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise NotFoundError("Permission override not found")
        effect = row.effect
        code = row.permission_code
        override_id = str(row.id)
        await self.db.delete(row)
        await self.db.flush()
        # Removing a GRANT takes the extra permission away. Removing a REVOKE restores the default.
        action = "permission.revoke" if effect == "GRANT" else "permission.grant"
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=actor.id,
            action=action,
            resource_type="membership_permission",
            resource_id=override_id,
            message=f"Cleared {effect} override for {code}",
            details={"membership_id": str(membership.id), "permission": code, "cleared_effect": effect},
        )

    async def list_memberships(self, school_id: UUID, page: PageParams) -> tuple[list[dict], int]:
        base = (
            select(SchoolMembership, User)
            .join(User, User.id == SchoolMembership.user_id)
            .where(SchoolMembership.school_id == school_id)
        )
        total = (await self.db.execute(select(func.count()).select_from(base.order_by(None).subquery()))).scalar_one()
        result = await self.db.execute(
            base.order_by(SchoolMembership.created_at.desc()).offset(page.offset).limit(page.page_size)
        )
        items = []
        for membership, user in result.all():
            items.append(
                {
                    "id": membership.id,
                    "user_id": membership.user_id,
                    "school_id": membership.school_id,
                    "role": membership.role,
                    "status": membership.status,
                    "email": user.email,
                    "full_name": user.full_name,
                }
            )
        return items, int(total)

    async def permissions_for_membership(self, school_id: UUID, membership_id: UUID) -> set[str]:
        membership = await self._membership(school_id, membership_id)
        user = CurrentUser(
            id=membership.user_id,
            email="",
            full_name="",
            role=UserRole(membership.role),
            school_id=school_id,
            membership_id=membership.id,
            is_platform_admin=False,
        )
        return await effective_permissions(self.db, user)

    async def recent_access_audit(self, school_id: UUID, limit: int = 50) -> list[AuditLog]:
        result = await self.db.execute(
            select(AuditLog)
            .where(
                AuditLog.school_id == school_id,
                AuditLog.action.in_(("permission.grant", "permission.revoke", "role.grant", "role.revoke")),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    def matrix() -> dict:
        return {
            "permissions": PERMISSIONS,
            "roles": {role.value: sorted(perms) for role, perms in ROLE_PERMISSIONS.items()},
        }
