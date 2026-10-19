import re
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.db.models import (
    Invitation,
    Parent,
    PlatformSchoolAccess,
    School,
    SchoolMembership,
    Student,
    Subscription,
    Teacher,
    User,
)
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.repository import hash_token
from src.modules.fees.tenant import bind_tenant
from src.modules.audit.repository import AuditRepository
from src.modules.platform.repository import PlatformRepository
from src.modules.platform.schemas import (
    SchoolAdminLoginCreate,
    SchoolAdminLoginOut,
    InvitationCreate,
    PlatformEmployeeCreate,
    PlatformEmployeeOut,
    PlatformEmployeeUpdate,
    SchoolCreate,
    SchoolDetailOut,
    SchoolOut,
    SchoolUpdate,
)
from src.modules.schools.provisioning import create_school_user
from src.shared.enums import InvitationStatus, MembershipStatus, PlatformRole, SchoolStatus, SubscriptionStatus, UserRole
from src.shared.utils import generate_token, utcnow


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return base or f"school-{secrets.token_hex(3)}"


def _name_key(name: str) -> str:
    """Normalise a school name for duplicate checks: 'St. Marry' == 'st marry'."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def _is_admin_or_above(platform_role: str | None) -> bool:
    return platform_role in {
        PlatformRole.SUPER_ADMIN.value,
        PlatformRole.ADMIN.value,
        PlatformRole.PLATFORM_ADMIN.value,
        PlatformRole.OPERATIONS.value,
    }


class PlatformService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PlatformRepository(db)
        self.audit = AuditRepository(db)

    async def _assert_school_access(self, actor: CurrentUser, school: School) -> None:
        if _is_admin_or_above(actor.platform_role):
            return
        if school.created_by_id == actor.id:
            return
        result = await self.db.execute(
            select(PlatformSchoolAccess).where(
                PlatformSchoolAccess.user_id == actor.id, PlatformSchoolAccess.school_id == school.id
            )
        )
        if result.scalar_one_or_none() is None:
            raise ForbiddenError("You do not have access to this school")

    async def _unique_slug(self, base: str) -> str:
        slug = base
        n = 0
        while await self.repo.get_by_slug(slug):
            n += 1
            slug = f"{base}-{n}"
        return slug

    async def _unique_school_code(self) -> str:
        for _ in range(30):
            code = f"SYB-{secrets.token_hex(3).upper()}"
            result = await self.db.execute(select(School).where(School.code == code))
            if result.scalar_one_or_none() is None:
                return code
        return f"SYB-{secrets.token_hex(4).upper()}"

    async def _plan_for_school(self, school_id: UUID) -> tuple[str | None, str | None]:
        result = await self.db.execute(
            select(Subscription.plan_code, Subscription.status)
            .where(Subscription.school_id == school_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        row = result.first()
        if not row:
            return None, None
        return row[0], row[1]

    async def _primary_admin(self, school_id: UUID) -> tuple[str | None, UUID | None, str | None]:
        result = await self.db.execute(
            select(User)
            .join(SchoolMembership, SchoolMembership.user_id == User.id)
            .where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
            )
            .order_by(SchoolMembership.created_at.asc())
            .limit(1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None, None, None
        return user.email, user.id, user.full_name

    async def _people_counts(self, school_id: UUID) -> tuple[int, int]:
        students = (
            await self.db.execute(
                select(func.count())
                .select_from(Student)
                .join(User, User.id == Student.user_id)
                .where(Student.school_id == school_id, User.status != "DELETED")
            )
        ).scalar_one()
        teachers = (
            await self.db.execute(select(func.count()).select_from(Teacher).where(Teacher.school_id == school_id))
        ).scalar_one()
        return students, teachers

    async def _to_school_out(self, school: School) -> SchoolOut:
        # Subscriptions and memberships are tenant tables under forced RLS; the platform
        # session carries no school, so bind this school before reading them.
        await bind_tenant(self.db, school.id)
        plan, sub_status = await self._plan_for_school(school.id)
        admin_email, admin_user_id, admin_name = await self._primary_admin(school.id)
        students, teachers = await self._people_counts(school.id)
        return SchoolOut(
            id=school.id,
            name=school.name,
            slug=school.slug,
            code=school.code,
            status=school.status,
            country=school.country,
            state=getattr(school, "state", None),
            city=school.city,
            address=school.address,
            postal_code=getattr(school, "postal_code", None),
            phone=school.phone,
            email=school.email,
            website=getattr(school, "website", None),
            timezone=school.timezone,
            currency=getattr(school, "currency", None) or "INR",
            logo_url=school.logo_url,
            plan_code=plan,
            subscription_status=sub_status,
            students_count=students,
            teachers_count=teachers,
            created_at=school.created_at,
            admin_email=admin_email,
            admin_user_id=admin_user_id,
            admin_name=admin_name,
        )

    async def _similar_school(self, name: str) -> School | None:
        key = _name_key(name)
        if not key:
            return None
        rows = (await self.db.execute(select(School).where(School.status != SchoolStatus.ARCHIVED.value))).scalars().all()
        return next((row for row in rows if _name_key(row.name) == key), None)

    async def create_school(self, actor: CurrentUser, data: SchoolCreate) -> SchoolOut:
        name = data.name.strip()
        if len(name) < 2:
            raise ValidationError("School name must be at least 2 characters")
        data.name = name
        if data.slug and await self.repo.get_by_slug(data.slug):
            raise ConflictError(f"The URL slug '{data.slug}' is already used by another school")
        if not data.allow_duplicate_name:
            twin = await self._similar_school(name)
            if twin is not None:
                raise ConflictError(
                    f"A school named '{twin.name}' already exists (slug {twin.slug}). "
                    "Open that school instead, or confirm to create another one."
                )
        if not data.admin.password:
            data.admin.password = cognito_svc.generate_temporary_password()
        base_slug = data.slug or _slugify(data.name)
        slug = await self._unique_slug(base_slug)
        code = await self._unique_school_code()

        # Tenant tables (subscriptions, memberships, audit) enforce
        # school_id = app.school_id under forced RLS. The platform admin's token
        # carries no school, so bind the new school's id before any insert.
        school_id = uuid4()
        await bind_tenant(self.db, school_id)

        school = School(
            id=school_id,
            name=data.name,
            slug=slug,
            code=code,
            country=data.country,
            state=data.state,
            city=data.city,
            address=data.address,
            postal_code=data.postal_code,
            phone=data.phone,
            email=str(data.email) if data.email else None,
            website=data.website,
            timezone=data.timezone,
            currency=data.currency or "INR",
            status=SchoolStatus.ACTIVE.value,
            created_by_id=actor.id,
        )
        await self.repo.create_school(school)
        self.db.add(
            Subscription(
                school_id=school.id,
                plan_code=data.plan_code,
                status=SubscriptionStatus.TRIAL.value,
                starts_at=utcnow(),
            )
        )
        await self.db.flush()

        user, _, temp = await create_school_user(
            self.db,
            school_id=school.id,
            email=data.admin.email,
            full_name=data.admin.full_name,
            password=data.admin.password,
            role=UserRole.SCHOOL_ADMIN,
        )
        await self.audit.write(
            action="platform.school.create",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="school",
            resource_id=str(school.id),
            message=f"School created: {school.name}",
            details={"admin_email": user.email, "plan_code": data.plan_code},
        )

        out = await self._to_school_out(school)
        out.temporary_password = temp or data.admin.password
        out.admin_email = user.email
        out.admin_user_id = user.id
        out.admin_name = user.full_name
        out.plan_code = data.plan_code
        return out

    async def update_school(self, actor: CurrentUser, school_id: UUID, data: SchoolUpdate) -> SchoolOut:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        await bind_tenant(self.db, school.id)
        payload = data.model_dump(exclude_unset=True)
        plan_code = payload.pop("plan_code", None)
        for k, v in payload.items():
            setattr(school, k, v)
        if plan_code:
            self.db.add(
                Subscription(
                    school_id=school.id,
                    plan_code=plan_code,
                    status=SubscriptionStatus.ACTIVE.value,
                    starts_at=utcnow(),
                )
            )
            await self.audit.write(
                action="platform.subscription.changed",
                actor_id=actor.id,
                school_id=school.id,
                resource_type="subscription",
                resource_id=str(school.id),
                message=f"Plan changed to {plan_code}",
                details={"plan_code": plan_code},
            )
        await self.db.flush()
        await self.audit.write(
            action="platform.school.update",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="school",
            resource_id=str(school.id),
            message=f"School updated: {school.name}",
            details={"fields": list(data.model_dump(exclude_unset=True).keys())},
        )
        return await self._to_school_out(school)

    async def set_school_status(
        self,
        actor: CurrentUser,
        school_id: UUID,
        *,
        status: str,
        reason: str | None = None,
        confirm_name: str | None = None,
    ) -> SchoolOut:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        await bind_tenant(self.db, school.id)
        if status == SchoolStatus.ARCHIVED.value:
            if confirm_name and confirm_name.strip() != school.name:
                raise ForbiddenError("Confirmation name does not match school name")
        school.status = status
        await self.db.flush()
        action = {
            SchoolStatus.SUSPENDED.value: "platform.school.suspend",
            SchoolStatus.ACTIVE.value: "platform.school.reactivate",
            SchoolStatus.ARCHIVED.value: "platform.school.archive",
        }.get(status, "platform.school.update")
        await self.audit.write(
            action=action,
            actor_id=actor.id,
            school_id=school.id,
            resource_type="school",
            resource_id=str(school.id),
            message=f"School status → {status}",
            details={"reason": reason},
        )
        return await self._to_school_out(school)

    async def get_school(self, actor: CurrentUser, school_id: UUID) -> SchoolDetailOut:
        from src.modules.platform.saas_service import PlatformSaasService

        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        base = await self._to_school_out(school)
        parents = (
            await self.db.execute(select(func.count()).select_from(Parent).where(Parent.school_id == school_id))
        ).scalar_one()
        admins = (
            await self.db.execute(
                select(func.count())
                .select_from(SchoolMembership)
                .where(
                    SchoolMembership.school_id == school_id,
                    SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
                )
            )
        ).scalar_one()
        entitlements = await PlatformSaasService(self.db).entitlements_for_plan_code(base.plan_code)
        onboarding = {
            "school_created": True,
            "admin_invited": bool(base.admin_email),
            "admin_accepted": bool(base.admin_email),
            "academic_year_configured": False,
            "grades_configured": False,
            "teacher_added": base.teachers_count > 0,
            "student_added": base.students_count > 0,
        }
        plan, sub_status = await self._plan_for_school(school_id)
        return SchoolDetailOut(
            **base.model_dump(),
            parents_count=parents,
            admins_count=admins,
            entitlements=entitlements,
            onboarding=onboarding,
            usage={
                "students": base.students_count,
                "teachers": base.teachers_count,
                "parents": parents,
            },
            subscription={
                "plan_code": plan,
                "status": sub_status,
            },
        )

    async def list_schools(
        self,
        actor: CurrentUser,
        page: int = 1,
        page_size: int = 20,
        *,
        q: str | None = None,
        status: str | None = None,
        plan_code: str | None = None,
        country: str | None = None,
        sort: str = "created_at",
    ) -> tuple[list[SchoolOut], int]:
        offset = (page - 1) * page_size
        items, total = await self.repo.list_schools_visible_to(
            actor_id=actor.id,
            is_admin=_is_admin_or_above(actor.platform_role),
            offset=offset,
            limit=page_size,
            q=q,
            status=status,
            plan_code=plan_code,
            country=country,
            sort=sort,
        )
        outs = [await self._to_school_out(s) for s in items]
        return outs, total

    async def list_school_admins(
        self,
        *,
        q: str | None = None,
        school_id: UUID | None = None,
        page: int = 1,
        page_size: int = 50,
    ):
        from src.modules.platform.schemas import SchoolAdminDirectoryOut

        offset = (page - 1) * page_size
        stmt = (
            select(User, SchoolMembership, School)
            .join(SchoolMembership, SchoolMembership.user_id == User.id)
            .join(School, School.id == SchoolMembership.school_id)
            .where(SchoolMembership.role == UserRole.SCHOOL_ADMIN.value)
            .order_by(User.created_at.desc())
        )
        if school_id:
            stmt = stmt.where(SchoolMembership.school_id == school_id)
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(User.email).like(like),
                    func.lower(User.full_name).like(like),
                    func.lower(School.name).like(like),
                )
            )
        total = (
            await self.db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
        ).scalar_one()
        rows = (await self.db.execute(stmt.offset(offset).limit(page_size))).all()
        items = [
            SchoolAdminDirectoryOut(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                school_id=school.id,
                school_name=school.name,
                role=mem.role,
                status=mem.status,
                last_login_at=user.last_login_at,
                created_at=user.created_at,
            )
            for user, mem, school in rows
        ]
        return items, total

    async def get_school_admin(self, admin_id: UUID) -> list:
        from src.modules.platform.schemas import SchoolAdminDirectoryOut

        stmt = (
            select(User, SchoolMembership, School)
            .join(SchoolMembership, SchoolMembership.user_id == User.id)
            .join(School, School.id == SchoolMembership.school_id)
            .where(
                User.id == admin_id,
                SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
            )
            .order_by(School.name.asc())
        )
        rows = (await self.db.execute(stmt)).all()
        if not rows:
            raise NotFoundError("School admin not found")
        return [
            SchoolAdminDirectoryOut(
                id=user.id,
                full_name=user.full_name,
                email=user.email,
                school_id=school.id,
                school_name=school.name,
                role=mem.role,
                status=mem.status,
                last_login_at=user.last_login_at,
                created_at=user.created_at,
            )
            for user, mem, school in rows
        ]

    async def global_search(self, q: str):
        from src.modules.platform.schemas import PlatformSearchHit, PlatformSearchResult
        from src.db.models import PlatformPlan

        like = f"%{q.lower()}%"
        schools = (
            await self.db.execute(
                select(School)
                .where(
                    or_(
                        func.lower(School.name).like(like),
                        func.lower(School.code).like(like),
                        func.lower(School.slug).like(like),
                    )
                )
                .order_by(School.name.asc())
                .limit(8)
            )
        ).scalars().all()
        admin_rows = (
            await self.db.execute(
                select(User, School)
                .join(SchoolMembership, SchoolMembership.user_id == User.id)
                .join(School, School.id == SchoolMembership.school_id)
                .where(
                    SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                    or_(
                        func.lower(User.email).like(like),
                        func.lower(User.full_name).like(like),
                    ),
                )
                .limit(8)
            )
        ).all()
        team = (
            await self.db.execute(
                select(User)
                .where(
                    User.is_platform_admin.is_(True),
                    or_(
                        func.lower(User.email).like(like),
                        func.lower(User.full_name).like(like),
                    ),
                )
                .limit(8)
            )
        ).scalars().all()
        plans = (
            await self.db.execute(
                select(PlatformPlan)
                .where(
                    or_(
                        func.lower(PlatformPlan.name).like(like),
                        func.lower(PlatformPlan.code).like(like),
                    )
                )
                .limit(8)
            )
        ).scalars().all()
        return PlatformSearchResult(
            query=q,
            schools=[
                PlatformSearchHit(
                    type="school",
                    id=str(s.id),
                    title=s.name,
                    subtitle=s.code,
                    href=f"/platform/schools/{s.id}",
                )
                for s in schools
            ],
            admins=[
                PlatformSearchHit(
                    type="admin",
                    id=str(u.id),
                    title=u.full_name,
                    subtitle=f"{u.email} · {school.name}",
                    href=f"/platform/admins/{u.id}",
                )
                for u, school in admin_rows
            ],
            team=[
                PlatformSearchHit(
                    type="team",
                    id=str(u.id),
                    title=u.full_name,
                    subtitle=f"{u.email} · {u.platform_role}",
                    href=f"/platform/team/{u.id}",
                )
                for u in team
            ],
            plans=[
                PlatformSearchHit(
                    type="plan",
                    id=str(p.id),
                    title=p.name,
                    subtitle=p.code,
                    href=f"/platform/plans/{p.id}",
                )
                for p in plans
            ],
        )

    async def invite_school_admin(
        self, actor: CurrentUser, school_id: UUID, data: InvitationCreate
    ) -> tuple[Invitation, str]:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        await bind_tenant(self.db, school.id)
        raw = generate_token(24)
        inv = Invitation(
            school_id=school_id,
            email=data.email.lower(),
            role=data.role if data.role in {r.value for r in UserRole} else UserRole.SCHOOL_ADMIN.value,
            token_hash=hash_token(raw),
            status=InvitationStatus.PENDING.value,
            invited_by=actor.id,
            expires_at=utcnow() + timedelta(days=data.expires_in_days),
            message=data.message,
        )
        await self.repo.create_invitation(inv)
        await self.audit.write(
            action="platform.invitation.create",
            actor_id=actor.id,
            school_id=school_id,
            resource_type="invitation",
            resource_id=str(inv.id),
            message=f"School admin invited: {inv.email}",
            details={"role": inv.role, "expires_at": inv.expires_at.isoformat()},
        )
        return inv, raw

    async def reset_school_admin_password(
        self, actor: CurrentUser, school_id: UUID, *, temporary_password: str | None = None
    ) -> tuple[str, str]:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        admin_email, _, _ = await self._primary_admin(school_id)
        if not admin_email:
            raise NotFoundError("School admin not found")
        from src.modules.schools.provisioning import issue_temporary_password

        user = (
            await self.db.execute(select(User).where(User.email == admin_email.lower().strip()))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User not found")
        # Cognito (or local fallback), sessions revoked, password change forced at next sign-in.
        temporary_password = await issue_temporary_password(
            self.db, user=user, temporary_password=temporary_password
        )
        await self.audit.write(
            action="platform.school.admin.reset_password",
            actor_id=actor.id,
            school_id=school_id,
            resource_type="user",
            resource_id=admin_email,
            message="School admin temporary password reset",
            details={"admin_email": admin_email},
        )
        return admin_email, temporary_password

    async def create_school_admin_login(self, actor: CurrentUser, school_id: UUID, data: SchoolAdminLoginCreate) -> SchoolAdminLoginOut:

        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        email = str(data.email).lower().strip()
        existing = (await self.db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing is not None:
            # Never reset someone else's password through this path.
            raise ConflictError("An account with this email already exists. Use a different email.")
        await bind_tenant(self.db, school.id)
        password = data.password or cognito_svc.generate_temporary_password()
        user, _, temp = await create_school_user(
            self.db,
            school_id=school.id,
            email=email,
            full_name=data.full_name.strip(),
            password=password,
            role=UserRole.SCHOOL_ADMIN,
            actor_id=actor.id,
        )
        await self.audit.write(
            action="platform.school.admin.create",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="user",
            resource_id=str(user.id),
            message=f"School admin login created: {user.email}",
            details={"admin_email": user.email},
        )
        return SchoolAdminLoginOut(
            user_id=user.id,
            admin_email=user.email,
            full_name=user.full_name,
            temporary_password=temp or password,
        )

    async def _reset_password(self, email: str, temporary_password: str) -> None:
        """Reset via Cognito when allowed; otherwise switch the account to a local hash.

        The API task role may lack Cognito admin permissions (AccessDeniedException).
        In that case (or when Cognito is not configured) the user signs in with the
        local password hash, which login prefers when cognito_sub is empty.
        """
        from src.core.security import hash_password

        user = (
            await self.db.execute(select(User).where(User.email == email.lower().strip()))
        ).scalar_one_or_none()
        if user is None:
            raise NotFoundError("User not found")
        if cognito_svc.cognito_configured() and user.cognito_sub:
            try:
                cognito_svc.admin_set_temporary_password(email=email, temporary_password=temporary_password)
                return
            except ConflictError as exc:
                if "AccessDenied" not in str(exc) or (get_settings().auth_mode or "both").lower() == "cognito":
                    raise
        user.password_hash = hash_password(temporary_password)
        user.cognito_sub = None
        user.is_active = True
        await self.db.flush()

    async def clear_school_admin_mfa(self, actor: CurrentUser, school_id: UUID) -> str:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        admin_email, _, _ = await self._primary_admin(school_id)
        if not admin_email:
            raise NotFoundError("School admin not found")
        cognito_svc.admin_clear_mfa(email=admin_email)
        await self.audit.write(
            action="platform.school.admin.clear_mfa",
            actor_id=actor.id,
            school_id=school_id,
            resource_type="user",
            resource_id=admin_email,
            message="School admin MFA cleared",
            details={"admin_email": admin_email},
        )
        return admin_email

    async def delete_school(self, actor: CurrentUser, school_id: UUID) -> None:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self.audit.write(
            action="platform.school.delete",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="school",
            resource_id=str(school.id),
            message=f"School deleted: {school.name}",
        )
        await self.db.delete(school)
        await self.db.flush()

    async def upload_school_logo(self, actor: CurrentUser, school_id: UUID, file: UploadFile) -> SchoolOut:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)

        content_type = (file.content_type or "").lower()
        ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
        ext = ext_map.get(content_type)
        if not ext:
            raise ConflictError("Logo must be PNG, JPG, or WEBP")

        data = await file.read()
        if not data:
            raise ConflictError("Empty upload")
        if len(data) > 2_000_000:
            raise ConflictError("Logo too large (max 2MB)")

        from src.core.storage import put_object

        school.logo_url = put_object(
            key=f"school-logos/{school.id}{ext}",
            data=data,
            content_type=content_type,
        )
        await self.db.flush()
        await self.audit.write(
            action="platform.school.logo.update",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="school",
            resource_id=str(school.id),
            message="School logo updated",
            details={"logo_url": school.logo_url},
        )
        return await self._to_school_out(school)

    async def dashboard(self):
        return await self.repo.dashboard_counts()

    async def list_employees(self) -> list[PlatformEmployeeOut]:
        result = await self.db.execute(
            select(User)
            .where(User.is_platform_admin.is_(True))
            .order_by(User.created_at.asc())
        )
        users = list(result.scalars().all())
        return [
            PlatformEmployeeOut(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                platform_role=u.platform_role or PlatformRole.USER.value,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login_at=u.last_login_at,
            )
            for u in users
        ]

    async def get_employee(self, employee_id: UUID) -> PlatformEmployeeOut:
        result = await self.db.execute(
            select(User).where(User.id == employee_id, User.is_platform_admin.is_(True))
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("Employee not found")
        return PlatformEmployeeOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            platform_role=user.platform_role or PlatformRole.USER.value,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def list_school_access(self, actor: CurrentUser, school_id: UUID) -> list[PlatformEmployeeOut]:
        if not _is_admin_or_above(actor.platform_role):
            raise ForbiddenError("Admin only")
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        result = await self.db.execute(
            select(User)
            .join(PlatformSchoolAccess, PlatformSchoolAccess.user_id == User.id)
            .where(PlatformSchoolAccess.school_id == school_id)
            .order_by(User.created_at.asc())
        )
        users = list(result.scalars().all())
        return [
            PlatformEmployeeOut(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                platform_role=u.platform_role or PlatformRole.USER.value,
                is_active=u.is_active,
                created_at=u.created_at,
                last_login_at=u.last_login_at,
            )
            for u in users
        ]

    async def grant_school_access(self, actor: CurrentUser, school_id: UUID, user_id: UUID) -> None:
        if not _is_admin_or_above(actor.platform_role):
            raise ForbiddenError("Admin only")
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        result = await self.db.execute(
            select(User).where(User.id == user_id, User.is_platform_admin.is_(True), User.is_active.is_(True))
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("User not found")
        if user.platform_role in {PlatformRole.SUPER_ADMIN.value, PlatformRole.ADMIN.value}:
            # Admins already have implicit access to all schools
            raise ConflictError("Cannot grant access to an admin account")
        try:
            self.db.add(PlatformSchoolAccess(user_id=user.id, school_id=school.id))
            await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError("Access already granted") from exc
        await self.audit.write(
            action="platform.school.access.grant",
            actor_id=actor.id,
            school_id=school.id,
            resource_type="user",
            resource_id=str(user.id),
            message=f"Granted school access to {user.email}",
        )

    async def revoke_school_access(self, actor: CurrentUser, school_id: UUID, user_id: UUID) -> None:
        if not _is_admin_or_above(actor.platform_role):
            raise ForbiddenError("Admin only")
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        result = await self.db.execute(
            select(PlatformSchoolAccess).where(
                PlatformSchoolAccess.school_id == school_id, PlatformSchoolAccess.user_id == user_id
            )
        )
        access = result.scalar_one_or_none()
        if access is None:
            return
        await self.db.delete(access)
        await self.db.flush()
        await self.audit.write(
            action="platform.school.access.revoke",
            actor_id=actor.id,
            school_id=school_id,
            resource_type="user",
            resource_id=str(user_id),
            message="Revoked school access",
        )

    async def create_employee(self, data: PlatformEmployeeCreate, actor: User) -> PlatformEmployeeOut:
        from src.core.security import hash_password

        if actor.platform_role not in {
            PlatformRole.SUPER_ADMIN.value,
            PlatformRole.ADMIN.value,
            PlatformRole.PLATFORM_ADMIN.value,
        }:
            raise ForbiddenError("Admin only")
        email = data.email.lower().strip()
        existing = await self.db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("User with this email already exists")

        requested_role = (data.platform_role or PlatformRole.USER.value).strip().upper()
        allowed = {r.value for r in PlatformRole} - {PlatformRole.SUPER_ADMIN.value}
        if requested_role not in allowed:
            raise ConflictError("Invalid platform role")
        if actor.platform_role in {PlatformRole.ADMIN.value, PlatformRole.PLATFORM_ADMIN.value} and requested_role not in {
            PlatformRole.USER.value,
            PlatformRole.SUPPORT.value,
            PlatformRole.ANALYST.value,
            PlatformRole.SALES.value,
        }:
            raise ForbiddenError("Admins can only create limited platform roles")
        if requested_role == PlatformRole.SUPER_ADMIN.value:
            raise ForbiddenError("Cannot create another SUPER_ADMIN via invite")

        cognito_sub: str | None = None
        password_hash: str | None = None
        if cognito_svc.cognito_configured():
            try:
                cognito_sub = cognito_svc.admin_create_user(
                    email=email,
                    temporary_password=data.password,
                    full_name=data.full_name.strip(),
                    make_permanent=False,
                )
            except Exception:
                # Fall back to local password so team invites still work in local/dev.
                password_hash = hash_password(data.password)
        else:
            password_hash = hash_password(data.password)

        user = User(
            email=email,
            full_name=data.full_name.strip(),
            password_hash=password_hash,
            cognito_sub=cognito_sub,
            is_platform_admin=True,
            platform_role=requested_role,
            is_active=True,
        )
        self.db.add(user)
        await self.db.flush()
        await self.audit.write(
            action="platform.employee.create",
            actor_id=actor.id,
            school_id=None,
            resource_type="user",
            resource_id=str(user.id),
            message=f"Platform user created: {user.email}",
            details={"platform_role": user.platform_role},
        )
        return PlatformEmployeeOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            platform_role=user.platform_role or PlatformRole.USER.value,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def update_employee(
        self, employee_id: UUID, data: PlatformEmployeeUpdate, actor: User
    ) -> PlatformEmployeeOut:
        if actor.platform_role not in {
            PlatformRole.SUPER_ADMIN.value,
            PlatformRole.ADMIN.value,
            PlatformRole.PLATFORM_ADMIN.value,
        }:
            raise ForbiddenError("Admin only")
        result = await self.db.execute(
            select(User).where(User.id == employee_id, User.is_platform_admin.is_(True))
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("Employee not found")
        if user.platform_role == PlatformRole.SUPER_ADMIN.value:
            raise ForbiddenError("Cannot modify the super admin account")
        if actor.platform_role in {PlatformRole.ADMIN.value, PlatformRole.PLATFORM_ADMIN.value} and user.platform_role not in {
            PlatformRole.USER.value,
            PlatformRole.SUPPORT.value,
            PlatformRole.ANALYST.value,
            PlatformRole.SALES.value,
        }:
            raise ForbiddenError("Admins can only modify limited platform roles")
        if data.full_name is not None:
            user.full_name = data.full_name.strip()
        if data.is_active is not None:
            user.is_active = data.is_active
            if user.cognito_sub and cognito_svc.cognito_configured():
                cognito_svc.admin_set_enabled(email=user.email, enabled=data.is_active)
        if data.platform_role is not None:
            if actor.platform_role != PlatformRole.SUPER_ADMIN.value:
                raise ForbiddenError("Only super admin can change platform roles")
            next_role = data.platform_role.strip().upper()
            allowed = {r.value for r in PlatformRole} - {PlatformRole.SUPER_ADMIN.value}
            if next_role not in allowed:
                raise ConflictError("Invalid platform role")
            user.platform_role = next_role
        await self.db.flush()
        await self.audit.write(
            action="platform.employee.update",
            actor_id=actor.id,
            school_id=None,
            resource_type="user",
            resource_id=str(user.id),
            message=f"Platform user updated: {user.email}",
            details={"is_active": user.is_active, "platform_role": user.platform_role},
        )
        return PlatformEmployeeOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            platform_role=user.platform_role or PlatformRole.USER.value,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )
