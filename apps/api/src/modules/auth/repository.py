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

    async def get_invitation_by_token_hash(self, token_hash: str) -> Invitation | None:
        result = await self.db.execute(select(Invitation).where(Invitation.token_hash == token_hash))
        return result.scalar_one_or_none()
