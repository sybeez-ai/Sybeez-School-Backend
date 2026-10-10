from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from src.core.security import (
    create_access_token,
    create_refresh_token_claims,
    encode_token,
    hash_password,
    verify_password,
)
from src.db.models import RefreshToken, SchoolMembership, User
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.repository import AuthRepository, hash_token
from src.modules.auth.schemas import AcceptInviteRequest, LoginRequest, TokenResponse
from src.shared.enums import InvitationStatus, MembershipStatus, PlatformRole, UserRole
from src.shared.utils import utcnow


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)

    def _token_response(
        self,
        *,
        user: User,
        role: UserRole,
        school_id,
        membership_id,
        access: str,
        refresh: str,
    ) -> TokenResponse:
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
            platform_role=user.platform_role if user.is_platform_admin else None,
        )

    async def _issue_session_for_user(
        self, user: User, *, school_id_hint=None, portal: str | None = None
    ) -> TokenResponse:
        if not user.is_active:
            raise UnauthorizedError("Account disabled")

        role = UserRole.PLATFORM_ADMIN
        school_id = None
        membership_id = None

        portal_key = (portal or "").strip().lower()
        wanted_roles = self._PORTAL_ROLES.get(portal_key) if portal_key else None

        if user.is_platform_admin and school_id_hint is None and (
            wanted_roles is None or UserRole.PLATFORM_ADMIN in wanted_roles
        ):
            role = UserRole.PLATFORM_ADMIN
            if not user.platform_role:
                user.platform_role = PlatformRole.USER.value
        else:
            membership = None
            if wanted_roles:
                for wanted in wanted_roles:
                    membership = await self.repo.get_membership_for_role(
                        user.id, wanted.value, school_id_hint
                    )
                    if membership:
                        break
            if membership is None:
                membership = await self.repo.get_membership(user.id, school_id_hint)
            if membership is None:
                if user.is_platform_admin:
                    role = UserRole.PLATFORM_ADMIN
                    if not user.platform_role:
                        user.platform_role = PlatformRole.USER.value
                else:
                    raise ForbiddenError("No school membership")
            else:
                role = UserRole(membership.role)
                school_id = membership.school_id
                membership_id = membership.id
                if membership.status != MembershipStatus.ACTIVE.value:
                    raise UnauthorizedError("School membership is not active")

        user.last_login_at = utcnow()
        jti = uuid4().hex
        extra = {"sid": jti}
        if user.is_platform_admin and user.platform_role:
            extra["platform_role"] = user.platform_role
        access = create_access_token(
            subject=user.id,
            role=role.value,
            school_id=school_id,
            membership_id=membership_id,
            extra=extra,
        )
        refresh = await self._issue_refresh(user.id, jti=jti, role=role, membership_id=membership_id)
        return self._token_response(
            user=user,
            role=role,
            school_id=school_id,
            membership_id=membership_id,
            access=access,
            refresh=refresh,
        )

    async def _user_from_cognito(self, cog: cognito_svc.CognitoAuthResult, email: str) -> User:
        user = await self.repo.get_user_by_email(cog.email or email)
        if user is None:
            user = User(
                email=cog.email or email,
                full_name=cog.email or email,
                password_hash=None,
                cognito_sub=cog.sub or None,
                is_platform_admin=False,
                is_active=True,
            )
            await self.repo.create_user(user)
        elif cog.sub and user.cognito_sub != cog.sub:
            user.cognito_sub = cog.sub
        return user


    _PORTAL_ROLES = {
        "platform": {UserRole.PLATFORM_ADMIN},
        "school": {UserRole.SCHOOL_ADMIN},
        "hrm": {UserRole.SCHOOL_ADMIN, UserRole.HR_MANAGER, UserRole.PAYROLL_APPROVER},
        "teacher": {UserRole.TEACHER},
        "student": {UserRole.STUDENT},
        "parent": {UserRole.PARENT},
    }

    async def _assert_portal_role(self, user: User, portal: str | None, *, school_id_hint=None) -> None:
        """Credentials created for a role may only sign in on that portal."""
        if not portal:
            return
        key = portal.strip().lower()
        allowed = self._PORTAL_ROLES.get(key)
        if allowed is None:
            raise ForbiddenError("Unknown login portal")

        role: UserRole | None = None
        if user.is_platform_admin and UserRole.PLATFORM_ADMIN in allowed and school_id_hint is None:
            role = UserRole.PLATFORM_ADMIN
        else:
            for wanted in allowed:
                membership = await self.repo.get_membership_for_role(
                    user.id, wanted.value, school_id_hint
                )
                if membership and membership.status == MembershipStatus.ACTIVE.value:
                    role = wanted
                    break
            if role is None:
                membership = await self.repo.get_membership(user.id, school_id_hint)
                if membership is None:
                    if user.is_platform_admin:
                        role = UserRole.PLATFORM_ADMIN
                    else:
                        raise ForbiddenError("No school membership for this account")
                else:
                    role = UserRole(membership.role)

        assert role is not None
        if role not in allowed:
            if role == UserRole.SCHOOL_ADMIN:
                raise ForbiddenError(
                    "This account is a school admin. Sign in at /login/school only."
                )
            if role == UserRole.HR_MANAGER:
                raise ForbiddenError(
                    "This account is an HR manager. Sign in at /login/hrm only."
                )
            if role == UserRole.PAYROLL_APPROVER:
                raise ForbiddenError(
                    "This account is a payroll approver. Sign in at /login/hrm only."
                )
            if role == UserRole.PLATFORM_ADMIN:
                raise ForbiddenError(
                    "This account is a platform admin. Sign in at /login/platform only."
                )
            if role == UserRole.TEACHER:
                raise ForbiddenError(
                    "This account is a teacher. Sign in at /login/teacher only."
                )
            if role == UserRole.STUDENT:
                raise ForbiddenError(
                    "This account is a student. Sign in at /login/student only."
                )
            if role == UserRole.PARENT:
                raise ForbiddenError(
                    "This account is a parent. Sign in at /login/parent only."
                )
            raise ForbiddenError(
                f"This account cannot sign in on the {key} portal."
            )


    async def login(self, data: LoginRequest) -> TokenResponse:
        settings = get_settings()
        mode = (settings.auth_mode or "both").lower()
        email = data.email.strip().lower()
        user: User | None = None
        cognito_ok = False

        # Seeded / local-password accounts (no Cognito sub) must win over Cognito.
        # Otherwise Dev login for student/school-admin can hit NEW_PASSWORD_REQUIRED
        # when the same email exists in the Cognito pool.
        if mode in {"local", "both"}:
            local_user = await self.repo.get_user_by_email(email)
            if (
                local_user is not None
                and local_user.is_active
                and local_user.password_hash
                and not local_user.cognito_sub
                and verify_password(data.password, local_user.password_hash)
            ):
                await self._assert_portal_role(
                    local_user, data.portal, school_id_hint=data.school_id
                )
                return await self._issue_session_for_user(
                    local_user, school_id_hint=data.school_id, portal=data.portal
                )

        if mode in {"cognito", "both"} and cognito_svc.cognito_configured():
            try:
                cog = cognito_svc.authenticate(email, data.password)
                if isinstance(cog, cognito_svc.CognitoNewPasswordChallenge):
                    # Enforce portal before allowing password-reset challenge.
                    existing = await self.repo.get_user_by_email(email)
                    if existing is not None:
                        await self._assert_portal_role(
                            existing, data.portal, school_id_hint=data.school_id
                        )
                    elif data.portal and data.portal.strip().lower() not in {"school", "hrm"}:
                        # Platform-provisioned Cognito users without a local row yet
                        # are school-scoped first-login accounts.
                        raise ForbiddenError(
                            "This account is a school admin. Sign in at /login/school or /hrm/login."
                        )
                    return TokenResponse(
                        challenge=cog.challenge,
                        session=cog.session,
                        email=cog.email,
                    )
                cognito_ok = True
                user = await self._user_from_cognito(cog, email)
            except ForbiddenError:
                raise
            except UnauthorizedError:
                if mode == "cognito":
                    raise
                cognito_ok = False
            except Exception:
                # Network/proxy/Cognito outages must not block local password login in "both" mode.
                if mode == "cognito":
                    raise UnauthorizedError("Identity provider unavailable")
                cognito_ok = False

        if not cognito_ok:
            if mode == "cognito":
                raise UnauthorizedError("Invalid email or password")
            user = await self.repo.get_user_by_email(email)
            if user is None or not user.password_hash or not verify_password(data.password, user.password_hash):
                raise UnauthorizedError("Invalid email or password")
            # School admins provisioned into Cognito must not fall back to a stale local hash
            # unless this is an explicit local-dev account (no Cognito sub).
            if user.cognito_sub and cognito_svc.cognito_configured():
                raise UnauthorizedError("Invalid email or password")

        assert user is not None
        await self._assert_portal_role(user, data.portal, school_id_hint=data.school_id)
        return await self._issue_session_for_user(
            user, school_id_hint=data.school_id, portal=data.portal
        )

    async def complete_new_password(self, data) -> TokenResponse:
        email = data.email.strip().lower()
        existing = await self.repo.get_user_by_email(email)
        cog = cognito_svc.complete_new_password(
            email=email,
            session=data.session,
            new_password=data.new_password,
            full_name=existing.full_name if existing else None,
        )
        user = await self._user_from_cognito(cog, email)
        # Cognito now owns the permanent password — drop any local hash
        user.password_hash = None
        await self._assert_portal_role(user, getattr(data, "portal", None))
        return await self._issue_session_for_user(
            user, portal=getattr(data, "portal", None)
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
        if user is None or not user.is_active:
            raise UnauthorizedError("Account disabled")

        # Prefer platform session for company operators even if they also have a school membership.
        previous_role = payload.get("role")
        session_membership = payload.get("membership_id")
        if session_membership:
            membership = await self.repo.get_membership_by_id(UUID(session_membership))
            if membership is None or membership.user_id != user.id:
                raise UnauthorizedError("School membership is not active")
        else:
            membership = await self.repo.get_membership(user.id)
        if user.is_platform_admin and (
            membership is None or previous_role == UserRole.PLATFORM_ADMIN.value
        ):
            role, school_id, membership_id = UserRole.PLATFORM_ADMIN, None, None
        elif membership:
            if membership.status != MembershipStatus.ACTIVE.value:
                raise UnauthorizedError("School membership is not active")
            role = UserRole(membership.role)
            school_id, membership_id = membership.school_id, membership.id
        elif user.is_platform_admin:
            role, school_id, membership_id = UserRole.PLATFORM_ADMIN, None, None
        else:
            raise ForbiddenError("No membership")

        new_jti = uuid4().hex
        stored.replaced_by = new_jti
        extra = {"sid": new_jti}
        if user.is_platform_admin and user.platform_role:
            extra["platform_role"] = user.platform_role
        access = create_access_token(
            subject=user.id,
            role=role.value,
            school_id=school_id,
            membership_id=membership_id,
            extra=extra,
        )
        refresh = await self._issue_refresh(user.id, jti=new_jti, role=role, membership_id=membership_id)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
            platform_role=user.platform_role if user.is_platform_admin else None,
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
        jti = uuid4().hex
        access = create_access_token(
            subject=user.id,
            role=role.value,
            school_id=inv.school_id,
            membership_id=membership.id,
            extra={"sid": jti},
        )
        refresh = await self._issue_refresh(user.id, jti=jti, role=role, membership_id=membership.id)
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=inv.school_id,
            user_id=user.id,
            full_name=user.full_name,
        )

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self.repo.revoke_refresh(refresh_token)

    async def forgot_password(self, email: str) -> None:
        """Always succeed at API level to avoid email enumeration; Cognito sends the code."""
        email = email.strip().lower()
        if cognito_svc.cognito_configured():
            cognito_svc.admin_forgot_password(email=email)

    async def confirm_forgot_password(
        self, *, email: str, confirmation_code: str, new_password: str
    ) -> None:
        email = email.strip().lower()
        if not cognito_svc.cognito_configured():
            raise ConflictError("Password reset via Cognito is not configured")
        cognito_svc.admin_confirm_forgot_password(
            email=email,
            confirmation_code=confirmation_code,
            new_password=new_password,
        )
        user = await self.repo.get_user_by_email(email)
        if user:
            # Local hash is unused when Cognito owns credentials
            user.password_hash = None
            await self.repo.revoke_all_for_user(user.id)

    async def update_profile(self, user_id, *, full_name: str) -> User:
        user = await self.repo.get_user(user_id)
        if user is None:
            raise NotFoundError("User not found")
        user.full_name = full_name.strip()
        await self.db.flush()
        return user

    async def _issue_refresh(
        self, user_id, jti: str | None = None, *, role: UserRole | None = None, membership_id=None
    ) -> str:
        jti = jti or uuid4().hex
        claims = create_refresh_token_claims(subject=user_id, jti=jti)
        # Refresh must renew the same session (portal role + school membership), not whichever
        # membership the user happens to have first.
        if role is not None:
            claims["role"] = role.value
        if membership_id is not None:
            claims["membership_id"] = str(membership_id)
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
