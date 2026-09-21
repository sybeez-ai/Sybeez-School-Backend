from datetime import timedelta
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from src.core.security import (
    create_access_token,
    create_refresh_token_claims,
    encode_token,
    hash_password,
    verify_password,
)
from src.db.models import RefreshToken, SchoolMembership, User
from src.modules.auth.repository import AuthRepository, hash_token
from src.modules.auth.schemas import AcceptInviteRequest, LoginRequest, TokenResponse
from src.shared.enums import InvitationStatus, MembershipStatus, UserRole
from src.shared.utils import utcnow


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)

    async def login(self, data: LoginRequest) -> TokenResponse:
        user = await self.repo.get_user_by_email(data.email)
        if user is None or not user.password_hash or not verify_password(data.password, user.password_hash):
            raise UnauthorizedError("Invalid email or password")

        role = UserRole.PLATFORM_ADMIN
        school_id = None
        membership_id = None

        if user.is_platform_admin and data.school_id is None:
            role = UserRole.PLATFORM_ADMIN
        else:
            membership = await self.repo.get_membership(user.id, data.school_id)
            if membership is None:
                if user.is_platform_admin:
                    role = UserRole.PLATFORM_ADMIN
                else:
                    raise ForbiddenError("No school membership")
            else:
                role = UserRole(membership.role)
                school_id = membership.school_id
                membership_id = membership.id

        user.last_login_at = utcnow()
        access = create_access_token(subject=user.id, role=role.value, school_id=school_id, membership_id=membership_id)
        refresh = await self._issue_refresh(user.id)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        from src.core.security import decode_token

        try:
            payload = decode_token(refresh_token)
        except ValueError as exc:
            raise UnauthorizedError("Invalid refresh token") from exc
        if payload.get("type") != "refresh":
            raise UnauthorizedError("Invalid token type")
        jti = payload["jti"]
        stored = await self.repo.get_refresh_by_jti(jti)
        if stored is None or stored.revoked or stored.expires_at < utcnow():
            raise UnauthorizedError("Refresh token revoked or expired")
        if stored.token_hash != hash_token(refresh_token):
            raise UnauthorizedError("Refresh token mismatch")

        stored.revoked = True
        user = await self.repo.get_user(stored.user_id)
        if user is None:
            raise UnauthorizedError("User not found")

        membership = await self.repo.get_membership(user.id)
        if user.is_platform_admin and membership is None:
            role, school_id, membership_id = UserRole.PLATFORM_ADMIN, None, None
        elif membership:
            role = UserRole(membership.role)
            school_id, membership_id = membership.school_id, membership.id
        else:
            raise ForbiddenError("No membership")

        new_jti = uuid4().hex
        stored.replaced_by = new_jti
        access = create_access_token(subject=user.id, role=role.value, school_id=school_id, membership_id=membership_id)
        refresh = await self._issue_refresh(user.id, jti=new_jti)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
        )

    async def accept_invitation(self, data: AcceptInviteRequest) -> TokenResponse:
        inv = await self.repo.get_invitation_by_token_hash(hash_token(data.token))
        if inv is None:
            raise NotFoundError("Invitation not found")
        if inv.status != InvitationStatus.PENDING.value:
            raise ConflictError("Invitation already used or revoked")
        if inv.expires_at < utcnow():
            inv.status = InvitationStatus.EXPIRED.value
            raise ForbiddenError("Invitation expired")

        user = await self.repo.get_user_by_email(inv.email)
        if user is None:
            user = User(
                email=inv.email.lower(),
                full_name=data.full_name,
                password_hash=hash_password(data.password),
                is_platform_admin=False,
            )
            await self.repo.create_user(user)
        else:
            if user.password_hash is None:
                user.password_hash = hash_password(data.password)
            user.full_name = data.full_name

        existing = await self.repo.get_membership(user.id, inv.school_id)
        if existing is None:
            membership = SchoolMembership(
                user_id=user.id,
                school_id=inv.school_id,
                role=inv.role,
                status=MembershipStatus.ACTIVE.value,
            )
            self.db.add(membership)
            await self.db.flush()
        else:
            membership = existing
            membership.status = MembershipStatus.ACTIVE.value
            membership.role = inv.role

        inv.status = InvitationStatus.ACCEPTED.value
        inv.accepted_at = utcnow()

        role = UserRole(inv.role)
        access = create_access_token(
            subject=user.id, role=role.value, school_id=inv.school_id, membership_id=membership.id
        )
        refresh = await self._issue_refresh(user.id)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=inv.school_id,
            user_id=user.id,
            full_name=user.full_name,
        )

    async def _issue_refresh(self, user_id, jti: str | None = None) -> str:
        jti = jti or uuid4().hex
        claims = create_refresh_token_claims(subject=user_id, jti=jti)
        raw = encode_token(claims)
        self.db.add(
            RefreshToken(
                user_id=user_id,
                jti=jti,
                token_hash=hash_token(raw),
                expires_at=utcnow() + timedelta(days=14),
            )
        )
        await self.db.flush()
        return raw
