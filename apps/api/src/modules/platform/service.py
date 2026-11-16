import re
import secrets
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from src.api.dependencies import CurrentUser
from src.core.config import get_settings
from src.db.models import (
    Employee,
    Enrollment,
    Invitation,
    Parent,
    PlatformSchoolAccess,
    PlatformStaff,
    School,
    SchoolMembership,
    Student,
    Subscription,
    Teacher,
    User,
)
from src.modules.auth import cognito as cognito_svc
from src.modules.auth.repository import hash_token
from src.modules.fees.tenant import bind_tenant, open_platform_school
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
from src.shared.enums import (
    EnrollmentStatus,
    InvitationStatus,
    MembershipStatus,
    PersonStatus,
    PlatformRole,
    SchoolStatus,
    SubscriptionStatus,
    UserRole,
)
from src.shared.utils import generate_token, utcnow


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]
    return base or f"school-{secrets.token_hex(3)}"


def _name_key(name: str) -> str:
    """Normalise a school name for duplicate checks: 'St. Marry' == 'st marry'."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def team_member_delete_block(actor: CurrentUser, user: User) -> str | None:
    """Why this team account cannot be removed, or None when removal is allowed."""
    from src.api.dependencies import is_protected_owner

    if user.id == actor.id:
        return "You cannot delete your own account"
    if is_protected_owner(user.email):
        return "The owner account cannot be removed"
    return None


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
        from src.modules.platform.commercial import default_trial_days, effective_trial_days

        plan, sub_status = await self._plan_for_school(school.id)
        admin_email, admin_user_id, admin_name = await self._primary_admin(school.id)
        students, teachers = await self._people_counts(school.id)
        platform_days = await default_trial_days(self.db)
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
            trial_days=getattr(school, "trial_days", None),
            effective_trial_days=effective_trial_days(getattr(school, "trial_days", None), platform_days),
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
        await open_platform_school(self.db, school_id)

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
        from src.modules.platform.commercial import default_trial_days

        trial_days = await default_trial_days(self.db)
        started = utcnow()
        self.db.add(
            Subscription(
                school_id=school.id,
                plan_code=data.plan_code,
                status=SubscriptionStatus.TRIAL.value,
                starts_at=started,
                trial_started_at=started,
                trial_ends_at=started + timedelta(days=trial_days),
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
        await open_platform_school(self.db, school.id)
        payload = data.model_dump(exclude_unset=True)
        previous_trial_days = school.trial_days
        plan_code = payload.pop("plan_code", None)
        for k, v in payload.items():
            setattr(school, k, v)
        if plan_code:
            from src.modules.platform.commercial import latest_subscription

            current = await latest_subscription(self.db, school.id)
            changed = current is None or current.plan_code != plan_code
            if current is None:
                self.db.add(
                    Subscription(
                        school_id=school.id,
                        plan_code=plan_code,
                        status=SubscriptionStatus.ACTIVE.value,
                        starts_at=utcnow(),
                    )
                )
            elif changed:
                current.plan_code = plan_code
            if changed:
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
            details={
                "fields": list(data.model_dump(exclude_unset=True).keys()),
                **(
                    {"trial_days": {"before": previous_trial_days, "after": school.trial_days}}
                    if "trial_days" in data.model_fields_set
                    else {}
                ),
            },
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
        await open_platform_school(self.db, school.id)
        if status in {SchoolStatus.ARCHIVED.value, SchoolStatus.SUSPENDED.value, SchoolStatus.ACTIVE.value}:
            if (confirm_name or "").strip() != (school.name or "").strip():
                raise ValidationError("Type the school name exactly to confirm this change")
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
        from src.modules.platform.commercial import commercial_view

        plan, sub_status = await self._plan_for_school(school_id)
        commercial = await commercial_view(self.db, school)
        creator = await self.db.get(User, school.created_by_id) if school.created_by_id else None
        return SchoolDetailOut(
            **base.model_dump(),
            created_by_id=school.created_by_id,
            created_by_name=creator.full_name if creator else None,
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
                "plan_code": commercial["plan_code"] or plan,
                "plan_name": commercial["plan_name"],
                "status": commercial["billing_status"] or sub_status,
                "subscription_status": sub_status,
                **commercial,
            },
            commercial=commercial,
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
            .where(
                SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
            )
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
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
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
                    SchoolMembership.status == MembershipStatus.ACTIVE.value,
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
                    href=f"/platform/staff",
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
        await open_platform_school(self.db, school.id)
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
        await open_platform_school(self.db, school.id)
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
        await open_platform_school(self.db, school.id)
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
        await open_platform_school(self.db, school.id)
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

    async def delete_school(self, actor: CurrentUser, school_id: UUID, confirm_name: str) -> None:
        """Erase the school and every row stored against it. The audit entry is kept."""
        from sqlalchemy import text

        from src.modules.fees.tenant import open_platform_school

        await open_platform_school(self.db, school_id)
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        if (confirm_name or "").strip() != (school.name or "").strip():
            raise ValidationError("Type the school name exactly to confirm deletion")
        await self._assert_school_access(actor, school)
        name = school.name
        await self.audit.write(
            action="platform.super_admin.delete",
            actor_id=actor.id,
            school_id=None,
            resource_type="school",
            resource_id=str(school_id),
            message=f"Super admin deleted school: {name}",
            details={"mode": "hard", "label": name, "confirm_name": confirm_name.strip(), "school_id": str(school_id)},
        )
        await self.db.flush()
        from src.modules.platform.purge import ForeignKey, missing_from_purge, purge_steps

        school_tables = {
            row[0]
            for row in (
                await self.db.execute(
                    text(
                        """
                        SELECT c.table_name
                        FROM information_schema.columns c
                        JOIN information_schema.tables t
                          ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                        WHERE c.table_schema = 'public'
                          AND c.column_name = 'school_id'
                          AND t.table_type = 'BASE TABLE'
                        """
                    )
                )
            ).all()
            if str(row[0]).replace("_", "").isalnum()
        }
        foreign_keys = [
            ForeignKey(
                child=str(child),
                parent=str(parent),
                child_cols=tuple(str(col) for col in child_cols),
                parent_cols=tuple(str(col) for col in parent_cols),
            )
            for child, parent, child_cols, parent_cols in (
                await self.db.execute(
                    text(
                        """
                        SELECT child.relname,
                               parent.relname,
                               array_agg(child_att.attname ORDER BY u.ord),
                               array_agg(parent_att.attname ORDER BY u.ord)
                        FROM pg_constraint fk
                        JOIN pg_class child ON child.oid = fk.conrelid
                        JOIN pg_class parent ON parent.oid = fk.confrelid
                        JOIN pg_namespace ns ON ns.oid = child.relnamespace
                        JOIN LATERAL unnest(fk.conkey, fk.confkey)
                          WITH ORDINALITY AS u(child_num, parent_num, ord) ON true
                        JOIN pg_attribute child_att
                          ON child_att.attrelid = child.oid AND child_att.attnum = u.child_num
                        JOIN pg_attribute parent_att
                          ON parent_att.attrelid = parent.oid AND parent_att.attnum = u.parent_num
                        WHERE fk.contype = 'f' AND ns.nspname = 'public'
                        GROUP BY fk.oid, child.relname, parent.relname
                        """
                    )
                )
            ).all()
        ]
        steps = purge_steps(school_tables, foreign_keys)
        planned = {step.table for step in steps}
        missing = missing_from_purge(school_tables, foreign_keys, planned)
        if missing:
            raise ConflictError(
                "Could not delete the school because these tables are not in the purge: "
                + ", ".join(missing[:8])
            )
        await self._delete_school_objects(school_id, school_tables, school.logo_url)
        people = [
            row[0]
            for row in (
                await self.db.execute(
                    text(
                        """
                        SELECT user_id FROM students WHERE school_id = :sid
                        UNION
                        SELECT user_id FROM teachers WHERE school_id = :sid
                        UNION
                        SELECT user_id FROM school_memberships WHERE school_id = :sid
                        """
                    ),
                    {"sid": school_id},
                )
            ).all()
        ]
        last_error: Exception | None = None
        pending = list(steps)
        for _ in range(6):
            if not pending:
                break
            stuck: list = []
            for step in pending:
                try:
                    async with self.db.begin_nested():
                        await self.db.execute(text(step.sql), {"sid": school_id})
                    left = (
                        await self.db.execute(
                            text(f'SELECT count(*) FROM "{step.table}" WHERE {step.where}'),
                            {"sid": school_id},
                        )
                    ).scalar_one()
                    if int(left or 0) > 0:
                        stuck.append(step)
                except Exception as exc:
                    last_error = exc
                    stuck.append(step)
            if not stuck:
                pending = []
                break
            if [step.table for step in stuck] == [step.table for step in pending]:
                pending = stuck
                break
            pending = stuck
        try:
            async with self.db.begin_nested():
                deleted = (
                    await self.db.execute(
                        text("DELETE FROM schools WHERE id = :sid RETURNING id"),
                        {"sid": school_id},
                    )
                ).first()
            if deleted is None:
                raise ConflictError("The school was not deleted. No school row changed.")
            # Fire deferred foreign keys now, inside this request, not in get_db()'s commit.
            await self.db.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        except ConflictError:
            raise
        except Exception as exc:
            left = ", ".join(step.table for step in pending[:8]) or "unknown"
            detail = str(getattr(exc, "orig", exc) or last_error or exc).splitlines()[0][:180]
            raise ConflictError(
                f"Could not delete the school because related rows remain ({left}). {detail}"
            ) from (last_error or exc)
        for person_id in people:
            if person_id == actor.id:
                continue
            try:
                async with self.db.begin_nested():
                    await self.db.execute(
                        text(
                            """
                            DELETE FROM users
                            WHERE id = :uid
                              AND COALESCE(is_platform_admin, false) = false
                              AND NOT EXISTS (
                                SELECT 1 FROM school_memberships m
                                WHERE m.user_id = users.id AND m.status = 'ACTIVE'
                              )
                            """
                        ),
                        {"uid": person_id},
                    )
            except Exception:
                continue
        await self.db.flush()

    async def _delete_school_objects(self, school_id: UUID, school_tables: set[str], logo_url: str | None) -> None:
        """Remove stored files for this school before the rows that name them are deleted."""
        from sqlalchemy import text

        from src.core.storage import delete_object

        delete_object(logo_url)
        names = sorted(table for table in school_tables if table != "schools" and table.replace("_", "").isalnum())
        if not names:
            return
        columns = ("file_key", "object_key", "logo_url", "logo_object_key", "pdf_object_key", "photo_url")
        found = (
            await self.db.execute(
                text(
                    """
                    SELECT c.table_name, c.column_name
                    FROM information_schema.columns c
                    WHERE c.table_schema = 'public'
                      AND c.column_name = ANY(:columns)
                      AND c.table_name = ANY(:tables)
                    """
                ),
                {"columns": list(columns), "tables": names},
            )
        ).all()
        for table, column in found:
            if not str(table).replace("_", "").isalnum() or not str(column).replace("_", "").isalnum():
                continue
            keys = (
                await self.db.execute(
                    text(f'SELECT "{column}" FROM "{table}" WHERE school_id = :sid'),
                    {"sid": school_id},
                )
            ).all()
            for (key,) in keys:
                if isinstance(key, str) and key:
                    delete_object(key)

    async def upload_school_logo(self, actor: CurrentUser, school_id: UUID, file: UploadFile) -> SchoolOut:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        await self._assert_school_access(actor, school)
        await open_platform_school(self.db, school.id)

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

    async def home_analytics(self) -> dict:
        """Real platform totals for the home page. No estimated money."""
        from datetime import timedelta

        from sqlalchemy import text

        from src.modules.fees.tenant import set_rls_bypass
        from src.shared.utils import utcnow

        await set_rls_bypass(self.db, True)
        now = utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        soon = now + timedelta(days=14)
        by_status = (
            await self.db.execute(select(School.status, func.count()).group_by(School.status))
        ).all()
        students = (await self.db.execute(select(func.count()).select_from(Student))).scalar_one()
        teachers = (await self.db.execute(select(func.count()).select_from(Teacher))).scalar_one()
        recent = (
            await self.db.execute(select(School).order_by(School.created_at.desc()).limit(8))
        ).scalars().all()
        trials = (
            await self.db.execute(
                text(
                    """
                    SELECT s.id, s.name, sub.trial_ends_at
                    FROM schools s
                    JOIN subscriptions sub ON sub.school_id = s.id
                    WHERE sub.trial_ends_at IS NOT NULL
                      AND sub.trial_ends_at >= :now
                      AND sub.trial_ends_at <= :soon
                    ORDER BY sub.trial_ends_at
                    LIMIT 12
                    """
                ),
                {"now": now, "soon": soon},
            )
        ).all()
        renewals = (
            await self.db.execute(
                text(
                    """
                    SELECT s.id, s.name, sub.renewal_at
                    FROM schools s
                    JOIN subscriptions sub ON sub.school_id = s.id
                    WHERE sub.renewal_at IS NOT NULL
                      AND sub.renewal_at >= :now
                      AND sub.renewal_at <= :soon
                    ORDER BY sub.renewal_at
                    LIMIT 12
                    """
                ),
                {"now": now, "soon": soon},
            )
        ).all()
        tokens = (
            await self.db.execute(
                text(
                    """
                    SELECT s.id, s.name, COALESCE(SUM(u.input_tokens + u.output_tokens), 0) AS tokens
                    FROM platform_ai_usage_daily u
                    JOIN schools s ON s.id = u.school_id
                    WHERE u.usage_date >= :start
                    GROUP BY s.id, s.name
                    ORDER BY tokens DESC
                    LIMIT 12
                    """
                ),
                {"start": month_start},
            )
        ).all()
        signups = (
            await self.db.execute(
                text(
                    """
                    SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month, COUNT(*)
                    FROM schools
                    WHERE created_at >= :start
                    GROUP BY 1
                    ORDER BY 1
                    """
                ),
                {"start": now - timedelta(days=180)},
            )
        ).all()
        return {
            "schools_by_status": [{"status": status, "count": int(count)} for status, count in by_status],
            "students_total": int(students or 0),
            "teachers_total": int(teachers or 0),
            "signups_by_month": [{"month": month, "count": int(count)} for month, count in signups],
            "trials_expiring": [
                {"school_id": row[0], "school_name": row[1], "trial_ends_at": row[2]} for row in trials
            ],
            "renewals_due": [
                {"school_id": row[0], "school_name": row[1], "renewal_at": row[2]} for row in renewals
            ],
            "ai_tokens_this_month": [
                {"school_id": row[0], "school_name": row[1], "tokens": int(row[2] or 0)} for row in tokens
            ],
            "recent_schools": [
                {"school_id": school.id, "school_name": school.name, "status": school.status, "created_at": school.created_at}
                for school in recent
            ],
        }

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

    async def remove_school_admin(self, actor: CurrentUser, school_id: UUID, user_id: UUID) -> None:
        """Revoke a school admin: membership, sessions, and role access."""
        from sqlalchemy import text

        from src.modules.fees.tenant import open_platform_school

        await open_platform_school(self.db, school_id)
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        membership = (
            await self.db.execute(
                select(SchoolMembership).where(
                    SchoolMembership.school_id == school_id,
                    SchoolMembership.user_id == user_id,
                    SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            raise NotFoundError("School admin not found")
        person = await self.db.get(User, user_id)
        stored = (
            await self.db.execute(
                text("SELECT status FROM school_memberships WHERE id = :id"),
                {"id": membership.id},
            )
        ).scalar_one()
        if stored != MembershipStatus.LEFT.value:
            changed = (
                await self.db.execute(
                    text(
                        """
                        UPDATE school_memberships
                        SET status = 'LEFT'
                        WHERE id = :id AND status IS DISTINCT FROM 'LEFT'
                        RETURNING id
                        """
                    ),
                    {"id": membership.id},
                )
            ).all()
            if len(changed) != 1:
                raise ConflictError("The admin was not removed. No membership changed.")
        await self.db.execute(
            text(
                """
                UPDATE refresh_tokens
                SET revoked = true
                WHERE user_id = :uid AND revoked IS NOT TRUE
                """
            ),
            {"uid": user_id},
        )
        status = (
            await self.db.execute(
                text("SELECT status FROM school_memberships WHERE id = :id"),
                {"id": membership.id},
            )
        ).scalar_one()
        if status != MembershipStatus.LEFT.value:
            raise ConflictError("The admin is still active. The membership update did not stick.")
        open_sessions = (
            await self.db.execute(
                text("SELECT count(*) FROM refresh_tokens WHERE user_id = :uid AND revoked IS NOT TRUE"),
                {"uid": user_id},
            )
        ).scalar_one()
        if int(open_sessions or 0) > 0:
            raise ConflictError("The admin's sessions were not revoked.")
        await self.db.flush()
        await self.audit.write(
            action="platform.super_admin.delete",
            actor_id=actor.id,
            school_id=school_id,
            resource_type="school_admin",
            resource_id=str(user_id),
            message=f"Super admin removed school admin: {person.full_name if person else user_id}",
            details={"mode": "revoke", "email": person.email if person else None, "label": school.name},
        )

    async def create_employee(self, data: PlatformEmployeeCreate, actor: User) -> PlatformEmployeeOut:
        from src.core.security import hash_password

        from src.api.dependencies import EMPLOYEE_ROLES, is_protected_owner, is_super_admin

        if not is_super_admin(actor):
            raise ForbiddenError("Only a Super Admin can grant employee access")
        email = data.email.lower().strip()
        if is_protected_owner(email):
            raise ForbiddenError("The owner account cannot be changed")
        existing = await self.db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            raise ConflictError("User with this email already exists")

        requested_role = (data.platform_role or PlatformRole.SALES.value).strip().upper().replace(" ", "_")
        if requested_role not in EMPLOYEE_ROLES:
            raise ConflictError("Role must be Super Admin, Admin, or Sales")

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
        from src.api.dependencies import EMPLOYEE_ROLES, is_protected_owner, is_super_admin

        if not is_super_admin(actor):
            raise ForbiddenError("Only a Super Admin can change employee access")
        result = await self.db.execute(
            select(User).where(User.id == employee_id, User.is_platform_admin.is_(True))
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("Employee not found")
        if is_protected_owner(user.email):
            raise ForbiddenError("The owner account cannot be changed")
        if (user.email or "").strip().lower() == "silverstalan.inbaraj@sybeez.ai" and (
            data.platform_role is not None or data.is_active is False
        ):
            raise ForbiddenError("This account stays Super Admin")
        if data.full_name is not None:
            user.full_name = data.full_name.strip()
        if data.is_active is not None:
            user.is_active = data.is_active
            if user.cognito_sub and cognito_svc.cognito_configured():
                cognito_svc.admin_set_enabled(email=user.email, enabled=data.is_active)
        if data.platform_role is not None:
            next_role = data.platform_role.strip().upper().replace(" ", "_")
            if next_role not in EMPLOYEE_ROLES:
                raise ConflictError("Role must be Super Admin, Admin, or Sales")
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

    async def restore_employee(self, employee_id: UUID, actor_user: User, platform_role: str | None = None) -> PlatformEmployeeOut:
        """Turn a revoked platform login back on. The person keeps or receives a console role."""
        from src.api.dependencies import EMPLOYEE_ROLES

        result = await self.db.execute(select(User).where(User.id == employee_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("Employee not found")
        role = (platform_role or user.platform_role or "SALES").strip().upper().replace(" ", "_")
        if role not in EMPLOYEE_ROLES:
            raise ConflictError("Role must be Super Admin, Admin, or Sales")
        user.is_active = True
        user.status = "ACTIVE"
        user.is_platform_admin = True
        user.platform_role = role
        if user.cognito_sub and cognito_svc.cognito_configured():
            cognito_svc.admin_set_enabled(email=user.email, enabled=True)
        await self.db.flush()
        await self.audit.write(
            action="platform.employee.restore",
            actor_id=actor_user.id,
            school_id=None,
            resource_type="user",
            resource_id=str(user.id),
            message=f"Platform access restored: {user.email}",
            details={"platform_role": role},
        )
        return PlatformEmployeeOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            platform_role=user.platform_role or role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )

    async def delete_employee(self, employee_id: UUID, actor: CurrentUser) -> None:
        """Remove a platform team login. The user row stays so audit history is not broken."""
        result = await self.db.execute(
            select(User).where(User.id == employee_id, User.is_platform_admin.is_(True))
        )
        from src.api.dependencies import is_super_admin

        user = result.scalar_one_or_none()
        if user is None:
            raise NotFoundError("Employee not found")
        if not is_super_admin(actor):
            raise ForbiddenError("Only a Super Admin can remove employee access")
        blocked = team_member_delete_block(actor, user)
        if blocked:
            raise ForbiddenError(blocked)
        label = user.full_name
        email = user.email
        user.is_active = False
        user.status = "DISABLED"
        user.is_platform_admin = False
        user.platform_role = None
        staff_rows = (
            await self.db.execute(select(PlatformStaff).where(PlatformStaff.user_id == user.id))
        ).scalars().all()
        for staff in staff_rows:
            staff.status = "REVOKED"
        if user.cognito_sub and cognito_svc.cognito_configured():
            cognito_svc.admin_set_enabled(email=email, enabled=False)
        await self.db.flush()
        await self.audit.write(
            action="platform.super_admin.delete",
            actor_id=actor.id,
            school_id=None,
            resource_type="user",
            resource_id=str(user.id),
            message=f"Super admin removed team member: {label}",
            details={"mode": "soft", "label": label, "email": email},
        )
