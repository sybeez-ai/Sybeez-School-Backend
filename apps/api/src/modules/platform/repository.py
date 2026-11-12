from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Invitation, PlatformSchoolAccess, School, Student, Subscription, Teacher, User
from src.shared.enums import InvitationStatus, SchoolStatus, SubscriptionStatus


class PlatformRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_school(self, school: School) -> School:
        self.db.add(school)
        await self.db.flush()
        return school

    async def get_school(self, school_id: UUID) -> School | None:
        result = await self.db.execute(select(School).where(School.id == school_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> School | None:
        result = await self.db.execute(select(School).where(School.slug == slug))
        return result.scalar_one_or_none()

    async def list_schools(self, *, offset: int, limit: int) -> tuple[list[School], int]:
        total = (await self.db.execute(select(func.count()).select_from(School))).scalar_one()
        result = await self.db.execute(
            select(School).order_by(School.created_at.desc()).offset(offset).limit(limit)
        )
        return list(result.scalars().all()), total

    async def list_schools_visible_to(
        self,
        *,
        actor_id: UUID,
        is_admin: bool,
        offset: int,
        limit: int,
        q: str | None = None,
        status: str | None = None,
        plan_code: str | None = None,
        country: str | None = None,
        sort: str = "created_at",
    ) -> tuple[list[School], int]:
        stmt = select(School)
        count_stmt = select(func.count(func.distinct(School.id))).select_from(School)

        if not is_admin:
            join_access = True
            stmt = stmt.outerjoin(PlatformSchoolAccess, PlatformSchoolAccess.school_id == School.id)
            count_stmt = count_stmt.outerjoin(
                PlatformSchoolAccess, PlatformSchoolAccess.school_id == School.id
            )
            visibility = or_(
                School.created_by_id == actor_id,
                PlatformSchoolAccess.user_id == actor_id,
            )
            stmt = stmt.where(visibility)
            count_stmt = count_stmt.where(visibility)
        else:
            join_access = False

        if status:
            stmt = stmt.where(School.status == status.upper())
            count_stmt = count_stmt.where(School.status == status.upper())
        if country:
            stmt = stmt.where(func.lower(School.country) == country.lower())
            count_stmt = count_stmt.where(func.lower(School.country) == country.lower())
        if plan_code:
            stmt = stmt.join(Subscription, Subscription.school_id == School.id).where(
                Subscription.plan_code == plan_code.lower()
            )
            count_stmt = count_stmt.join(Subscription, Subscription.school_id == School.id).where(
                Subscription.plan_code == plan_code.lower()
            )
        if q:
            like = f"%{q.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(School.name).like(like),
                    func.lower(School.code).like(like),
                    func.lower(School.slug).like(like),
                    func.lower(School.email).like(like),
                )
            )
            count_stmt = count_stmt.where(
                or_(
                    func.lower(School.name).like(like),
                    func.lower(School.code).like(like),
                    func.lower(School.slug).like(like),
                    func.lower(School.email).like(like),
                )
            )

        order = School.created_at.desc()
        if sort == "name":
            order = School.name.asc()
        elif sort == "status":
            order = School.status.asc()

        total = (await self.db.execute(count_stmt)).scalar_one()
        result = await self.db.execute(stmt.order_by(order).offset(offset).limit(limit))
        # Deduplicate if joins created duplicates
        seen: set[UUID] = set()
        schools: list[School] = []
        for s in result.scalars().all():
            if s.id in seen:
                continue
            seen.add(s.id)
            schools.append(s)
        return schools, total

    async def create_invitation(self, inv: Invitation) -> Invitation:
        self.db.add(inv)
        await self.db.flush()
        return inv

    async def list_invitations(self, school_id: UUID) -> list[Invitation]:
        result = await self.db.execute(
            select(Invitation).where(Invitation.school_id == school_id).order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    async def dashboard_counts(self) -> dict:
        from src.modules.platform.schemas import AttentionItem, MetricPoint, PlatformDashboard
        from src.db.models import Parent

        schools = (await self.db.execute(select(func.count()).select_from(School))).scalar_one()
        active = (
            await self.db.execute(
                select(func.count()).select_from(School).where(School.status == SchoolStatus.ACTIVE.value)
            )
        ).scalar_one()
        suspended = (
            await self.db.execute(
                select(func.count()).select_from(School).where(School.status == SchoolStatus.SUSPENDED.value)
            )
        ).scalar_one()
        trial = (
            await self.db.execute(
                select(func.count(func.distinct(Subscription.school_id)))
                .select_from(Subscription)
                .where(Subscription.status == SubscriptionStatus.TRIAL.value)
            )
        ).scalar_one()
        pending = (
            await self.db.execute(
                select(func.count())
                .select_from(Invitation)
                .where(Invitation.status == InvitationStatus.PENDING.value)
            )
        ).scalar_one()
        students = (
            await self.db.execute(
                select(func.count())
                .select_from(Student)
                .join(User, User.id == Student.user_id)
                .where(User.status != "DELETED")
            )
        ).scalar_one()
        teachers = (await self.db.execute(select(func.count()).select_from(Teacher))).scalar_one()
        parents = (await self.db.execute(select(func.count()).select_from(Parent))).scalar_one()
        active_users = (
            await self.db.execute(select(func.count()).select_from(User).where(User.is_active.is_(True)))
        ).scalar_one()

        # Schools without admin
        schools_with_admin = (
            await self.db.execute(
                select(func.count(func.distinct(School.id)))
                .select_from(School)
                .join(Invitation, Invitation.school_id == School.id)
            )
        ).scalar_one()
        attention: list[AttentionItem] = []
        if pending:
            attention.append(
                AttentionItem(code="pending_invites", message=f"{pending} pending invitations", severity="warn")
            )
        if suspended:
            attention.append(
                AttentionItem(code="suspended", message=f"{suspended} suspended schools", severity="warn")
            )
        no_admin = max(0, schools - schools_with_admin)
        if no_admin:
            attention.append(
                AttentionItem(
                    code="missing_admin",
                    message=f"{no_admin} schools may need admin attention",
                    severity="info",
                )
            )

        return PlatformDashboard(
            schools_count=schools,
            active_schools=active,
            trial_schools=trial,
            suspended_schools=suspended,
            pending_invitations=pending,
            total_students=students,
            total_teachers=teachers,
            total_parents=parents,
            active_users=active_users,
            attention=attention,
            school_growth=[MetricPoint(label="Total", value=schools)],
            user_growth=[
                MetricPoint(label="Students", value=students),
                MetricPoint(label="Teachers", value=teachers),
                MetricPoint(label="Parents", value=parents),
            ],
        ).model_dump()
