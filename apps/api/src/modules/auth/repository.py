from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Invitation, RefreshToken, SchoolMembership, User


def hash_token(raw: str) -> str:
    return sha256(raw.encode()).hexdigest()


class AuthRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self.db.execute(select(User).where(User.email == email.lower()))
        return result.scalar_one_or_none()

    async def get_user(self, user_id: UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create_user(self, user: User) -> User:
        self.db.add(user)
        await self.db.flush()
        return user

    async def get_membership(self, user_id: UUID, school_id: UUID | None = None) -> SchoolMembership | None:
        q = select(SchoolMembership).where(SchoolMembership.user_id == user_id)
        if school_id:
            q = q.where(SchoolMembership.school_id == school_id)
        result = await self.db.execute(q.limit(1))
        return result.scalar_one_or_none()

    async def get_membership_by_id(self, membership_id: UUID) -> SchoolMembership | None:
        return await self.db.get(SchoolMembership, membership_id)

    async def get_membership_for_role(
        self,
        user_id: UUID,
        role: str,
        school_id: UUID | None = None,
    ) -> SchoolMembership | None:
        """Prefer membership matching the login portal role (teacher/student/…)."""
        q = select(SchoolMembership).where(
            SchoolMembership.user_id == user_id,
            SchoolMembership.role == role,
        )
        if school_id:
            q = q.where(SchoolMembership.school_id == school_id)
        result = await self.db.execute(q.limit(1))
        return result.scalar_one_or_none()

    async def list_memberships(self, user_id: UUID) -> list[SchoolMembership]:
        result = await self.db.execute(select(SchoolMembership).where(SchoolMembership.user_id == user_id))
        return list(result.scalars().all())

    async def save_refresh(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.flush()
        return token

    async def get_refresh_by_jti(self, jti: str) -> RefreshToken | None:
        result = await self.db.execute(select(RefreshToken).where(RefreshToken.jti == jti))
        return result.scalar_one_or_none()

    async def revoke_refresh(self, refresh_token: str) -> None:
        from src.core.security import decode_token

        try:
            payload = decode_token(refresh_token)
        except ValueError:
            return
        if payload.get("type") != "refresh":
            return
        jti = payload.get("jti")
        if not jti:
            return
        stored = await self.get_refresh_by_jti(jti)
        if stored and not stored.revoked:
            stored.revoked = True

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False))
        )
        for tok in result.scalars().all():
            tok.revoked = True

    async def get_invitation_by_token_hash(self, token_hash: str) -> Invitation | None:
        result = await self.db.execute(select(Invitation).where(Invitation.token_hash == token_hash))
        return result.scalar_one_or_none()
