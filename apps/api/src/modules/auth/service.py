from datetime import timedelta
from uuid import UUID, uuid4

from src.core.sessions import session_lifetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from src.core.security import (
    create_access_token,
    create_refresh_token_claims,
    encode_token,
    hash_password,
    verify_password,
)
from src.db.models import RefreshToken, School, SchoolMembership, User
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.repository import AuthRepository, hash_token
from src.modules.auth.schemas import (
    AcceptInviteRequest,
    LoginRequest,
    MembershipOut,
    TokenResponse,
)
from src.shared.enums import InvitationStatus, MembershipStatus, PlatformRole, UserRole
from src.shared.utils import utcnow


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = AuthRepository(db)
        self._refresh_max_age = 0

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
        settings = get_settings()
        return TokenResponse(
            access_token=access,
            refresh_token=refresh,
            role=role.value,
            school_id=school_id,
            user_id=user.id,
            full_name=user.full_name,
            platform_role=user.platform_role if user.is_platform_admin else None,
            access_expires_in=settings.access_token_expire_minutes * 60,
            refresh_expires_in=self._refresh_max_age,
        )

    async def _membership_outs(self, rows: list[SchoolMembership]) -> list[MembershipOut]:
        out: list[MembershipOut] = []
        for membership in rows:
            school = await self.db.get(School, membership.school_id)
            out.append(
                MembershipOut(
                    id=membership.id,
                    school_id=membership.school_id,
                    school_name=school.name if school else "",
                    role=UserRole(membership.role),
                    status=membership.status,
                )
            )
        out.sort(key=lambda row: (row.school_name.lower(), row.role.value))
        return out

    async def _active_memberships(self, user_id: UUID) -> list[SchoolMembership]:
        rows = await self.repo.list_memberships(user_id)
        return [row for row in rows if row.status == MembershipStatus.ACTIVE.value]

    async def _platform_staff(self, user_id: UUID):
        from src.db.models import PlatformStaff

        return (
            await self.db.execute(
                select(PlatformStaff).where(
                    PlatformStaff.user_id == user_id, PlatformStaff.status == "ACTIVE"
                )
            )
        ).scalar_one_or_none()

    async def _issue_session_for_user(
        self,
        user: User,
        *,
        school_id_hint=None,
        portal: str | None = None,
        membership_id: UUID | None = None,
    ) -> TokenResponse:
        if not user.is_active:
            raise UnauthorizedError("Account disabled")

        role = UserRole.PLATFORM_ADMIN
        school_id = None
        chosen_membership = None

        portal_key = (portal or "").strip().lower()
        wanted_roles = self._PORTAL_ROLES.get(portal_key) if portal_key else None
        active = await self._active_memberships(user.id)
        if membership_id is not None:
            chosen_membership = next((row for row in active if row.id == membership_id), None)
            if chosen_membership is None:
                raise ForbiddenError("No active membership for that selection")
            if school_id_hint is not None and chosen_membership.school_id != school_id_hint:
                raise ForbiddenError("No active membership for that selection")
        elif wanted_roles and UserRole.PLATFORM_ADMIN not in wanted_roles:
            for wanted in wanted_roles:
                match = next((row for row in active if row.role == wanted.value), None)
                if school_id_hint is not None and match is not None and match.school_id != school_id_hint:
                    match = next(
                        (
                            row
                            for row in active
                            if row.role == wanted.value and row.school_id == school_id_hint
                        ),
                        None,
                    )
                if match is not None:
                    chosen_membership = match
                    break

        needs_pick = (
            chosen_membership is None
            and wanted_roles is None
            and not (user.is_platform_admin and school_id_hint is None and membership_id is None and len(active) == 0)
        )
        if chosen_membership is None and school_id_hint is not None:
            scoped = [row for row in active if row.school_id == school_id_hint]
            if len(scoped) == 1:
                chosen_membership = scoped[0]
            elif len(scoped) > 1:
                return TokenResponse(
                    selection_required=True,
                    memberships=await self._membership_outs(scoped),
                    user_id=user.id,
                    full_name=user.full_name,
                    email=user.email,
                    platform_login_available=user.is_platform_admin,
                )
            needs_pick = False
        if needs_pick and len(active) > 1:
            return TokenResponse(
                selection_required=True,
                memberships=await self._membership_outs(active),
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                platform_login_available=user.is_platform_admin,
            )
        if needs_pick and len(active) == 1 and user.is_platform_admin and school_id_hint is None:
            return TokenResponse(
                selection_required=True,
                memberships=await self._membership_outs(active),
                user_id=user.id,
                full_name=user.full_name,
                email=user.email,
                platform_login_available=True,
            )
        if chosen_membership is None and len(active) == 1 and not (
            user.is_platform_admin and school_id_hint is None and wanted_roles is not None and UserRole.PLATFORM_ADMIN in wanted_roles
        ):
            if wanted_roles is None or UserRole(active[0].role) in wanted_roles:
                chosen_membership = active[0]

        if chosen_membership is not None:
            role = UserRole(chosen_membership.role)
            school_id = chosen_membership.school_id
            membership_id = chosen_membership.id
        elif user.is_platform_admin and (wanted_roles is None or UserRole.PLATFORM_ADMIN in wanted_roles):
            role = UserRole.PLATFORM_ADMIN
            membership_id = None
            if not user.platform_role:
                user.platform_role = PlatformRole.SUPER_ADMIN.value if (
                    user.email.lower() == get_settings().superadmin_email.lower()
                ) else PlatformRole.USER.value
        elif await self._platform_staff(user.id) is not None and not active:
            role = UserRole.PLATFORM_STAFF
            membership_id = None
        else:
            raise ForbiddenError("No school membership")

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
        refresh = await self._issue_refresh(
            user.id,
            jti=jti,
            role=role,
            school_id=school_id,
            membership_id=membership_id,
        )
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
        "platform": {UserRole.PLATFORM_ADMIN, UserRole.PLATFORM_STAFF},
        "school": {UserRole.SCHOOL_ADMIN},
        "hrm": {UserRole.SCHOOL_ADMIN, UserRole.HR, UserRole.HR_MANAGER, UserRole.PAYROLL_APPROVER},
        "payroll": {
            UserRole.SCHOOL_ADMIN,
            UserRole.PAYROLL_ADMIN,
            UserRole.HR_MANAGER,
            UserRole.ACCOUNTANT,
            UserRole.PAYROLL_APPROVER,
            UserRole.AUDITOR,
        },
        "finance": {
            UserRole.SCHOOL_ADMIN,
            UserRole.ACCOUNTANT,
            UserRole.CASHIER,
            UserRole.AUDITOR,
        },
        "teacher": {UserRole.TEACHER},
        "student": {UserRole.STUDENT},
        "parent": {UserRole.PARENT},
        "staff": {UserRole.STAFF},
    }

    # Login portal key -> platform staff portal grant (platform_staff_portals.portal).
    _STAFF_PORTAL_KEYS = {
        "platform": "platform",
        "school": "school_admin",
        "hrm": "hrm",
        "payroll": "payroll",
        "finance": "finance",
    }

    async def _staff_portals_without_membership(self, user_id: UUID) -> set[str] | None:
        """Granted portals for an active platform staff member with no school membership."""
        from src.db.models import PlatformStaffPortal

        staff = await self._platform_staff(user_id)
        if staff is None or await self._active_memberships(user_id):
            return None
        rows = await self.db.execute(
            select(PlatformStaffPortal.portal).where(PlatformStaffPortal.staff_id == staff.id)
        )
        return set(rows.scalars().all())

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
                    staff_portals = (
                        None
                        if user.is_platform_admin or school_id_hint is not None
                        else await self._staff_portals_without_membership(user.id)
                    )
                    if staff_portals is not None:
                        granted = self._STAFF_PORTAL_KEYS.get(key)
                        if granted and granted in staff_portals:
                            return
                        raise ForbiddenError(
                            f"This account has not been granted the {key} portal. "
                            "Ask a Sybeez super admin to grant access."
                        )
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
            if role == UserRole.HR:
                raise ForbiddenError(
                    "This account is an HR user. Sign in at /login/hrm only."
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
            if role == UserRole.STAFF:
                raise ForbiddenError(
                    "This account is a staff member. Sign in at /login/staff only."
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
                    local_user,
                    school_id_hint=data.school_id,
                    portal=data.portal,
                    membership_id=data.membership_id,
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
                    elif data.portal and data.portal.strip().lower() not in {
                        "school",
                        "hrm",
                        "payroll",
                        "finance",
                    }:
                        # Platform-provisioned Cognito users without a local row yet
                        # are school-scoped first-login accounts.
                        raise ForbiddenError(
                            "This account is a school admin. Sign in at /login/school, /hrm/login, /school/payroll/login, or /finance/login."
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
            user,
            school_id_hint=data.school_id,
            portal=data.portal,
            membership_id=data.membership_id,
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
        if stored.absolute_session and stored.family_expires_at <= utcnow():
            raise UnauthorizedError("Session expired. Sign in again.")

        stored.revoked = True
        user = await self.repo.get_user(stored.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account disabled")

        # Prefer the school/role stamped on the refresh token (switch-school).
        # Older refresh tokens omit it and fall back to the first membership.
        previous_role = payload.get("role")
        session_membership = payload.get("membership_id")
        hinted_school = payload.get("school_id")
        membership = None
        if session_membership:
            membership = await self.repo.get_membership_by_id(UUID(str(session_membership)))
            if membership is None or membership.user_id != user.id:
                raise UnauthorizedError("School membership is not active")
            if membership.status != MembershipStatus.ACTIVE.value:
                raise UnauthorizedError("School membership is not active")
        elif hinted_school:
            school_uuid = UUID(str(hinted_school))
            if previous_role and previous_role != UserRole.PLATFORM_ADMIN.value:
                membership = await self.repo.get_membership_for_role(
                    user.id, previous_role, school_uuid
                )
            if membership is None:
                membership = await self.repo.get_membership(user.id, school_uuid)
            if membership is None or membership.status != MembershipStatus.ACTIVE.value:
                raise ForbiddenError("School membership is no longer active")
        elif user.is_platform_admin and (
            previous_role == UserRole.PLATFORM_ADMIN.value or previous_role is None
        ):
            membership = await self.repo.get_membership(user.id)
            if membership is None or previous_role == UserRole.PLATFORM_ADMIN.value:
                membership = None
        else:
            membership = await self.repo.get_membership(user.id)

        if membership is None and user.is_platform_admin and hinted_school is None:
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
        refresh = await self._issue_refresh(
            user.id,
            jti=new_jti,
            role=role,
            school_id=school_id,
            membership_id=membership_id,
            family_expires_at=stored.family_expires_at,
            absolute=stored.absolute_session,
        )
        return self._token_response(
            user=user,
            role=role,
            school_id=school_id,
            membership_id=membership_id,
            access=access,
            refresh=refresh,
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

        role = UserRole(inv.role)
        jti = uuid4().hex
        access = create_access_token(
            subject=user.id,
            role=role.value,
            school_id=inv.school_id,
            membership_id=membership.id,
            extra={"sid": jti},
        )
        refresh = await self._issue_refresh(
            user.id,
            jti=jti,
            role=role,
            school_id=inv.school_id,
            membership_id=membership.id,
        )
        return self._token_response(
            user=user,
            role=role,
            school_id=inv.school_id,
            membership_id=membership.id,
            access=access,
            refresh=refresh,
        )

    async def accept_staff_invitation(self, data: AcceptInviteRequest) -> TokenResponse:
        from src.modules.platform.staff_service import StaffService

        user, _staff = await StaffService(self.db).accept(
            token=data.token, full_name=data.full_name, password=data.password
        )
        return await self._issue_session_for_user(user)

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

    async def list_memberships(self, user_id: UUID) -> list[MembershipOut]:
        rows = await self.repo.list_memberships(user_id)
        out: list[MembershipOut] = []
        for membership in rows:
            school = await self.db.get(School, membership.school_id)
            out.append(
                MembershipOut(
                    id=membership.id,
                    school_id=membership.school_id,
                    school_name=school.name if school else "",
                    role=UserRole(membership.role),
                    status=membership.status,
                )
            )
        out.sort(key=lambda row: (row.school_name.lower(), row.role.value))
        return out

    async def switch_school(
        self, user_id: UUID, school_id: UUID, role: UserRole | None = None
    ) -> TokenResponse:
        user = await self.repo.get_user(user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Account disabled")
        matches = [
            membership
            for membership in await self.repo.list_memberships(user.id)
            if membership.school_id == school_id
            and membership.status == MembershipStatus.ACTIVE.value
        ]
        if role is not None:
            matches = [membership for membership in matches if membership.role == role.value]
        if not matches:
            raise ForbiddenError("No active membership for that school")
        if len(matches) > 1:
            raise AppError(
                "Specify role when you hold more than one membership at this school",
                status_code=422,
            )
        membership = matches[0]
        chosen = UserRole(membership.role)
        jti = uuid4().hex
        extra = {"sid": jti}
        if user.is_platform_admin and user.platform_role:
            extra["platform_role"] = user.platform_role
        access = create_access_token(
            subject=user.id,
            role=chosen.value,
            school_id=membership.school_id,
            membership_id=membership.id,
            extra=extra,
        )
        refresh = await self._issue_refresh(
            user.id,
            jti=jti,
            role=chosen,
            school_id=membership.school_id,
            membership_id=membership.id,
        )
        return self._token_response(
            user=user,
            role=chosen,
            school_id=membership.school_id,
            membership_id=membership.id,
            access=access,
            refresh=refresh,
        )

    async def _issue_refresh(
        self,
        user_id,
        jti: str | None = None,
        *,
        role: str | UserRole | None = None,
        school_id=None,
        membership_id=None,
        family_expires_at=None,
        absolute: bool | None = None,
    ) -> str:
        jti = jti or uuid4().hex
        role_value = role.value if isinstance(role, UserRole) else role
        lifetime, default_absolute = session_lifetime(role or UserRole.STUDENT)
        now = utcnow()
        absolute_session = default_absolute if absolute is None else absolute
        if family_expires_at is None:
            family_expires_at = now + lifetime
        expires_at = family_expires_at if absolute_session else now + lifetime
        if expires_at > family_expires_at:
            expires_at = family_expires_at
        claims = create_refresh_token_claims(
            subject=user_id,
            jti=jti,
            expires_at=expires_at,
            role=role_value,
            school_id=school_id,
            membership_id=membership_id,
        )
        raw = encode_token(claims)
        self.db.add(
            RefreshToken(
                user_id=user_id,
                jti=jti,
                token_hash=hash_token(raw),
                expires_at=expires_at,
                family_expires_at=family_expires_at,
                absolute_session=absolute_session,
            )
        )
        self._refresh_max_age = max(0, int((expires_at - now).total_seconds()))
        return raw
