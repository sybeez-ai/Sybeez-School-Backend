from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, NotFoundError
from src.db.models import Invitation, School, Subscription
from src.modules.access.audit import write_audit
from src.modules.auth.repository import hash_token
from src.modules.platform.repository import PlatformRepository
from src.modules.platform.schemas import InvitationCreate, SchoolCreate, SchoolOut, SchoolUpdate
from src.modules.schools.provisioning import create_school_user
from src.shared.enums import InvitationStatus, SchoolStatus, SubscriptionStatus, UserRole
from src.shared.utils import generate_token, utcnow


class PlatformService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = PlatformRepository(db)

    async def create_school(self, data: SchoolCreate) -> SchoolOut:
        if await self.repo.get_by_slug(data.slug):
            raise ConflictError("School slug already exists")
        school = School(
            name=data.name,
            slug=data.slug,
            country=data.country,
            city=data.city,
            address=data.address,
            timezone=data.timezone,
            status=SchoolStatus.ACTIVE.value,
            code=data.slug.upper()[:8],
        )
        await self.repo.create_school(school)
        self.db.add(
            Subscription(
                school_id=school.id,
                plan_code="standard",
                status=SubscriptionStatus.TRIAL.value,
                starts_at=utcnow(),
            )
        )
        await self.db.flush()

        user, _ = await create_school_user(
            self.db,
            school_id=school.id,
            email=data.admin.email,
            full_name=data.admin.full_name,
            password=data.admin.password,
            role=UserRole.SCHOOL_ADMIN,
        )

        return SchoolOut(
            id=school.id,
            name=school.name,
            slug=school.slug,
            code=school.code,
            status=school.status,
            country=school.country,
            city=school.city,
            timezone=school.timezone,
            created_at=school.created_at,
            admin_email=user.email,
            admin_user_id=user.id,
        )

    async def update_school(self, school_id: UUID, data: SchoolUpdate) -> School:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        for k, v in data.model_dump(exclude_unset=True).items():
            setattr(school, k, v)
        await self.db.flush()
        return school

    async def get_school(self, school_id: UUID) -> School:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        return school

    async def list_schools(self, page: int = 1, page_size: int = 20):
        offset = (page - 1) * page_size
        return await self.repo.list_schools(offset=offset, limit=page_size)

    async def invite_school_admin(self, school_id: UUID, data: InvitationCreate, invited_by: UUID) -> tuple[Invitation, str]:
        school = await self.repo.get_school(school_id)
        if school is None:
            raise NotFoundError("School not found")
        raw = generate_token(24)
        inv = Invitation(
            school_id=school_id,
            email=data.email.lower(),
            role=data.role if data.role in {r.value for r in UserRole} else UserRole.SCHOOL_ADMIN.value,
            token_hash=hash_token(raw),
            status=InvitationStatus.PENDING.value,
            invited_by=invited_by,
            expires_at=utcnow() + timedelta(days=data.expires_in_days),
            message=data.message,
        )
        await self.repo.create_invitation(inv)
        await write_audit(
            self.db,
            school_id=school_id,
            actor_id=invited_by,
            action="role.grant",
            resource_type="invitation",
            resource_id=str(inv.id),
            message=f"Platform invited {inv.email} as {inv.role}",
            details={"email": inv.email, "role": inv.role, "status": "pending"},
        )
        return inv, raw

    async def dashboard(self):
        return await self.repo.dashboard_counts()
