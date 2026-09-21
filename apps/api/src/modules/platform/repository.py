from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Invitation, School
from src.shared.enums import InvitationStatus, SchoolStatus


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
        result = await self.db.execute(select(School).order_by(School.created_at.desc()).offset(offset).limit(limit))
        return list(result.scalars().all()), total

    async def create_invitation(self, inv: Invitation) -> Invitation:
        self.db.add(inv)
        await self.db.flush()
        return inv

    async def list_invitations(self, school_id: UUID) -> list[Invitation]:
        result = await self.db.execute(
            select(Invitation).where(Invitation.school_id == school_id).order_by(Invitation.created_at.desc())
        )
        return list(result.scalars().all())

    async def dashboard_counts(self) -> dict[str, int]:
        schools = (await self.db.execute(select(func.count()).select_from(School))).scalar_one()
        active = (
            await self.db.execute(
                select(func.count()).select_from(School).where(School.status == SchoolStatus.ACTIVE.value)
            )
        ).scalar_one()
        pending = (
            await self.db.execute(
                select(func.count())
                .select_from(Invitation)
                .where(Invitation.status == InvitationStatus.PENDING.value)
            )
        ).scalar_one()
        return {"schools_count": schools, "active_schools": active, "pending_invitations": pending}
