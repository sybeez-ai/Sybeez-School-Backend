"""Provision school-scoped users (admin/teacher/student/parent) with Cognito + memberships."""

import logging

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.exceptions import ConflictError
from src.core.rls import clear_lookup_email, set_lookup_email
from src.core.security import hash_password
from src.db.models import SchoolMembership, User
from src.modules.access.audit import write_audit
from src.modules.auth import cognito as cognito_svc
from src.shared.enums import MembershipStatus, UserRole

logger = logging.getLogger(__name__)

# Every admin-issued temporary password must be replaced at first sign-in, for all roles
# (the web AuthProvider and the mobile app both handle must_change_password).


async def get_or_create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str | None,
    cognito_permanent: bool = False,
    first_name: str | None = None,
    middle_name: str | None = None,
    last_name: str | None = None,
) -> tuple[User, str | None]:
    """Create/update local user and Cognito account.

    Returns (user, temporary_password_used).
    When Cognito is configured, a password is always required (auto-generated if omitted).
    """
    email = email.lower().strip()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    temp_used: str | None = None
    cognito_sub: str | None = None
    used_cognito = False

    parts = [p for p in (first_name, middle_name, last_name) if p and p.strip()]
    resolved_full = " ".join(parts) if parts else full_name.strip()

    if cognito_svc.cognito_configured():
        temp_used = password or cognito_svc.generate_temporary_password()
        try:
            cognito_sub = cognito_svc.admin_create_user(
                email=email,
                temporary_password=temp_used,
                full_name=resolved_full,
                make_permanent=cognito_permanent,
            )
            used_cognito = True
        except cognito_svc.CognitoAccessDeniedError:
            # auth_mode=both: when the API role may not call Cognito admin APIs, fall back
            # to a local password hash (same behaviour as provision_superadmin). Login uses
            # the local hash while cognito_sub is empty. auth_mode=cognito still fails.
            if (get_settings().auth_mode or "both").lower().strip() == "cognito":
                raise
            logger.warning("Cognito admin API denied; provisioning %s with a local password", email)
            password = temp_used
    if not used_cognito and not password and user is None and not cognito_svc.cognito_configured():
        # Without Cognito, a blank password used to leave a new account with no credential
        # at all (no hash, nothing shown to the admin, no reset path), so it could never
        # sign in. New accounts always get a temporary password the admin sees once.
        password = cognito_svc.generate_temporary_password()
    if not used_cognito and password:
        temp_used = password

    if user:
        if used_cognito:
            user.password_hash = None
        elif password:
            user.password_hash = hash_password(password)
        if resolved_full and user.full_name != resolved_full:
            user.full_name = resolved_full
        if first_name is not None:
            user.first_name = first_name.strip() or None
        if middle_name is not None:
            user.middle_name = middle_name.strip() or None
        if last_name is not None:
            user.last_name = last_name.strip() or None
        if cognito_sub:
            user.cognito_sub = cognito_sub
        user.is_active = True
        return user, temp_used

    user = User(
        email=email,
        full_name=resolved_full,
        first_name=(first_name or "").strip() or None,
        middle_name=(middle_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        password_hash=None if used_cognito else (hash_password(password) if password else None),
        cognito_sub=cognito_sub,
        is_platform_admin=False,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user, temp_used


async def ensure_membership(
    db: AsyncSession,
    *,
    user_id: UUID,
    school_id: UUID,
    role: UserRole,
    actor_id: UUID | None = None,
) -> SchoolMembership:
    result = await db.execute(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user_id,
            SchoolMembership.school_id == school_id,
            SchoolMembership.role == role.value,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        if membership.status != MembershipStatus.ACTIVE.value:
            membership.status = MembershipStatus.ACTIVE.value
            await write_audit(
                db,
                school_id=school_id,
                actor_id=actor_id,
                action="role.grant",
                resource_type="membership",
                resource_id=str(membership.id),
                message=f"Reactivated role {role.value}",
                details={"user_id": str(user_id), "role": role.value, "status": "reactivated"},
            )
        return membership
    membership = SchoolMembership(
        user_id=user_id,
        school_id=school_id,
        role=role.value,
        status=MembershipStatus.ACTIVE.value,
    )
    db.add(membership)
    await db.flush()
    await write_audit(
        db,
        school_id=school_id,
        actor_id=actor_id,
        action="role.grant",
        resource_type="membership",
        resource_id=str(membership.id),
        message=f"Granted role {role.value}",
        details={"user_id": str(user_id), "role": role.value},
    )
    return membership


async def create_school_user(
    db: AsyncSession,
    *,
    school_id: UUID,
    email: str,
    full_name: str,
    password: str | None,
    role: UserRole,
    first_name: str | None = None,
    middle_name: str | None = None,
    last_name: str | None = None,
    actor_id: UUID | None = None,
) -> tuple[User, SchoolMembership, str | None]:
    """Create school user in DB + Cognito. Returns (user, membership, temporary_password)."""
    user, temp = await get_or_create_user(
        db,
        email=email,
        full_name=full_name,
        password=password,
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
    )
    if user.is_platform_admin and role != UserRole.PLATFORM_ADMIN:
        raise ConflictError("Cannot assign platform admin as school role via this path")
    membership = await ensure_membership(
        db, user_id=user.id, school_id=school_id, role=role, actor_id=actor_id
    )
    if temp:
        # An admin-issued password is temporary: the person must choose their own at first login.
        user.must_change_password = True
    return user, membership, temp


async def issue_temporary_password(
    db: AsyncSession, *, user: User, temporary_password: str | None = None
) -> str:
    """Reset a school user's login to a new temporary password (shown once to the admin).

    Uses Cognito when the account lives there and the API may call it; otherwise the
    account switches to a local password hash (production API role is denied Cognito
    admin calls). Existing sessions are revoked and a password change is forced.
    """
    from sqlalchemy import update

    from src.db.models import RefreshToken

    temp = temporary_password or cognito_svc.generate_temporary_password()
    done = False
    if cognito_svc.cognito_configured() and user.cognito_sub:
        try:
            cognito_svc.admin_set_temporary_password(email=user.email, temporary_password=temp)
            done = True
        except ConflictError as exc:
            if "AccessDenied" not in str(exc) or (get_settings().auth_mode or "both").lower() == "cognito":
                raise
    if not done:
        user.password_hash = hash_password(temp)
        user.cognito_sub = None
    user.is_active = True
    user.must_change_password = True
    await db.execute(
        update(RefreshToken).where(RefreshToken.user_id == user.id).values(revoked=True)
    )
    await db.flush()
    return temp


async def set_user_active(db: AsyncSession, *, user: User, active: bool) -> User:
    user.is_active = active
    if user.email:
        cognito_svc.admin_set_enabled(email=user.email, enabled=active)
    await db.flush()
    return user
