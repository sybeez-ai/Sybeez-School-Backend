"""School-scoped user activation and lookup."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.exceptions import ForbiddenError, NotFoundError
from src.db.models import SchoolMembership, User
from src.modules.schools.provisioning import set_user_active
from src.modules.users.schemas import SchoolUserOut
from src.shared.enums import MembershipStatus


class UsersService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def set_active(
        self, actor: CurrentUser, user_id: UUID, *, is_active: bool
    ) -> SchoolUserOut:
        target = (await self.db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if target is None:
            raise NotFoundError("User not found")
        if target.is_platform_admin and not actor.is_platform_admin:
            raise ForbiddenError("Cannot change platform admin status")

        membership = None
        if actor.school_id:
            membership = (
                await self.db.execute(
                    select(SchoolMembership).where(
                        SchoolMembership.user_id == user_id,
                        SchoolMembership.school_id == actor.school_id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None and not actor.is_platform_admin:
                raise ForbiddenError("User is not a member of your school")
            if membership is not None:
                membership.status = (
                    MembershipStatus.ACTIVE.value if is_active else MembershipStatus.SUSPENDED.value
                )

        await set_user_active(self.db, user=target, active=is_active)
        return SchoolUserOut(
            id=target.id,
            email=target.email,
            full_name=target.full_name,
            is_active=bool(target.is_active),
            role=membership.role if membership else None,
            school_id=actor.school_id,
        )
