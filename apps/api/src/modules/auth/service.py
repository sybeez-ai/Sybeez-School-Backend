from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.cognito import cognito_password_ok
from src.core.config import get_settings
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from src.core.security import (
    create_access_token,
    create_refresh_token_claims,
    encode_token,
    hash_password,
    verify_password,
)
from src.core.sessions import session_lifetime
from src.db.models import PlatformStaff, RefreshToken, SchoolMembership, User
from src.modules.access.audit import write_audit
from src.modules.auth.repository import AuthRepository, hash_token
from src.modules.auth.schemas import AcceptInviteRequest, LoginRequest, TokenResponse
from src.shared.enums import InvitationStatus, MembershipStatus, UserRole
from src.shared.utils import utcnow


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)

    def _password_ok(self, user: User, plain: str) -> bool:
        settings = get_settings()
        if settings.auth_provider.strip().lower() == "cognito" and user.is_platform_admin and not user.password_hash:
            return cognito_password_ok(user.email, plain)
        if not user.password_hash:
            return False
        return verify_password(plain, user.password_hash)

    async def _active_staff(self, user_id: UUID) -> PlatformStaff | None:
        return (
            await self.db.execute(
                select(PlatformStaff).where(PlatformStaff.user_id == user_id, PlatformStaff.status == "ACTIVE")
            )
        ).scalar_one_or_none()

    async def _active_membership(self, user_id: UUID, school_id: UUID | None) -> SchoolMembership | None:
        query = select(SchoolMembership).where(
            SchoolMembership.user_id == user_id,
            SchoolMembership.status == MembershipStatus.ACTIVE.value,
        )
        if school_id is not None:
            query = query.where(SchoolMembership.school_id == school_id)
        return (await self.db.execute(query.limit(1))).scalar_one_or_none()

    async def _resolve_context(
        self, user: User, school_id: UUID | None
    ) -> tuple[UserRole, UUID | None, UUID | None]:
        if school_id is not None:
            membership = await self._active_membership(user.id, school_id)
            if membership is None:
                if user.is_platform_admin:
                    return UserRole.PLATFORM_ADMIN, school_id, None
                raise ForbiddenError("No school membership")
            return UserRole(membership.role), membership.school_id, membership.id

        staff = await self._active_staff(user.id)
        if staff is not None and not user.is_platform_admin:
            return UserRole.PLATFORM_STAFF, None, None
        if user.is_platform_admin and staff is None:
            return UserRole.PLATFORM_ADMIN, None, None
        membership = await self._active_membership(user.id, None)
        if membership is not None:
            return UserRole(membership.role), membership.school_id, membership.id
        if user.is_platform_admin:
            return UserRole.PLATFORM_ADMIN, None, None
        raise ForbiddenError("No school membership")

    def _schedule(self, role: UserRole, *, family_expires_at=None, absolute: bool | None = None):
        ttl, is_absolute = session_lifetime(role)
        now = utcnow()
        if is_absolute:
            cap = now + ttl
            if absolute and family_expires_at is not None:
                family = family_expires_at if family_expires_at <= cap else cap
            else:
                family = cap
            if family <= now:
                raise UnauthorizedError("Session expired. Sign in again.")
            return family, True
        return now + ttl, False

    async def _issue(
        self,
        user: User,
        role: UserRole,
        school_id: UUID | None,
        membership_id: UUID | None,
        *,
        family_expires_at=None,
        absolute: bool | None = None,
        replaced_jti: str | None = None,
    ) -> TokenResponse:
        expires_at, absolute_session = self._schedule(role, family_expires_at=family_expires_at, absolute=absolute)
        jti = replaced_jti or uuid4().hex
        access = create_access_token(
            subject=user.id, role=role.value, school_id=school_id, membership_id=membership_id
        )
        claims = create_refresh_token_claims(subject=user.id, jti=jti, expires_at=expires_at)
        refresh = encode_token(claims)
        self.db.add(
            RefreshToken(
                user_id=user.id,
                jti=jti,
                token_hash=hash_token(refresh),
                expires_at=expires_at,
                family_expires_at=expires_at,
                absolute_session=absolute_session,
            )
        )
        await self.db.flush()
        now = utcnow()
        settings = get_settings()
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
            access_expires_in=settings.access_token_expire_minutes * 60,
            refresh_expires_in=max(0, int((expires_at - now).total_seconds())),
        )

    async def login(self, data: LoginRequest) -> TokenResponse:
        user = await self.repo.get_user_by_email(data.email)
        if user is None or not self._password_ok(user, data.password):
            raise UnauthorizedError("Invalid email or password")
        role, school_id, membership_id = await self._resolve_context(user, data.school_id)
        user.last_login_at = utcnow()
        return await self._issue(user, role, school_id, membership_id)

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
        if stored.absolute_session and stored.family_expires_at <= utcnow():
            raise UnauthorizedError("Session expired. Sign in again.")

        stored.revoked = True
        user = await self.repo.get_user(stored.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("User not found")
        role, school_id, membership_id = await self._resolve_context(user, None)
        new_jti = uuid4().hex
        stored.replaced_by = new_jti
        return await self._issue(
            user,
            role,
            school_id,
            membership_id,
            family_expires_at=stored.family_expires_at,
            absolute=stored.absolute_session,
            replaced_jti=new_jti,
        )

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        from src.core.security import decode_token

        try:
            payload = decode_token(refresh_token)
        except ValueError:
            return
        if payload.get("type") != "refresh":
            return
        stored = await self.repo.get_refresh_by_jti(payload.get("jti", ""))
        if stored is not None:
            stored.revoked = True
            await self.db.flush()

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
        await write_audit(
            self.db,
            school_id=inv.school_id,
            actor_id=user.id,
            action="role.grant",
            resource_type="membership",
            resource_id=str(membership.id),
            message=f"Accepted invitation as {inv.role}",
            details={"email": inv.email, "role": inv.role, "invitation_id": str(inv.id), "status": "accepted"},
        )
        return await self._issue(user, UserRole(inv.role), inv.school_id, membership.id)

    async def accept_staff_invitation(self, data: AcceptInviteRequest) -> TokenResponse:
        from src.modules.platform.staff_service import StaffService

        user, _staff = await StaffService(self.db).accept(
            token=data.token, full_name=data.full_name, password=data.password
        )
        return await self._issue(user, UserRole.PLATFORM_STAFF, None, None)
