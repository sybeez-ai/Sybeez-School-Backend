"""Provision school-scoped users (admin/teacher/student/parent) with memberships."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError
from src.core.rls import clear_lookup_email, set_lookup_email
from src.core.security import hash_password
from src.db.models import SchoolMembership, User
from src.modules.access.audit import write_audit
from src.shared.enums import MembershipStatus, UserRole


async def get_or_create_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    password: str | None,
) -> User:
    email = email.lower().strip()
    # Lookup email stays set through INSERT ... RETURNING. Users RLS would hide a
    # brand-new account until a membership exists, and the insert would fail.
    await set_lookup_email(db, email)
    try:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=hash_password(password) if password else None,
                is_platform_admin=False,
                is_active=True,
            )
            db.add(user)
            await db.flush()
            return user
        # Do not rewrite a password that already belongs to another school.
        if password and user.password_hash is None:
            user.password_hash = hash_password(password)
            if full_name:
                user.full_name = full_name
            await db.flush()
        return user
    finally:
        await clear_lookup_email(db)


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
    actor_id: UUID | None = None,
) -> tuple[User, SchoolMembership]:
    # Prevent duplicate email colliding with another platform admin unexpectedly — allow reuse across schools
    user = await get_or_create_user(db, email=email, full_name=full_name, password=password)
    if user.is_platform_admin and role != UserRole.PLATFORM_ADMIN:
        raise ConflictError("Cannot assign platform admin as school role via this path")
    membership = await ensure_membership(
        db, user_id=user.id, school_id=school_id, role=role, actor_id=actor_id
    )
    return user, membership
