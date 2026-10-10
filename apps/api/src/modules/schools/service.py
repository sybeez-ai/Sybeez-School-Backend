from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import EmployeeStatutoryProfile, School, SchoolHoliday, WeeklyOffRule
from src.modules.payroll.reports import distinct_branches


class SchoolsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_my_school(self, school_id: UUID | None) -> School:
        if school_id is None:
            raise ForbiddenError("School context required")
        result = await self.db.execute(select(School).where(School.id == school_id))
        school = result.scalar_one_or_none()
        if school is None:
            raise NotFoundError("School not found")
        return school

    async def list_branches(self, school_id: UUID | None) -> list[str]:
        """Branch names this school already uses on holidays, weekly offs, and staff profiles."""
        if school_id is None:
            raise ForbiddenError("School context required")
        names: list[str | None] = []
        for column, model in (
            (WeeklyOffRule.branch_name, WeeklyOffRule),
            (SchoolHoliday.branch_name, SchoolHoliday),
            (EmployeeStatutoryProfile.branch_name, EmployeeStatutoryProfile),
        ):
            rows = (
                await self.db.execute(
                    select(column).where(model.school_id == school_id, column.is_not(None), column != "")
                )
            ).scalars()
            names.extend(rows)
        return distinct_branches(names)

    async def update_my_school(self, school_id: UUID | None, *, name: str | None = None) -> School:
        school = await self.get_my_school(school_id)
        if name is not None and name.strip():
            school.name = name.strip()
        await self.db.flush()
        return school

    async def upload_my_school_logo(self, school_id: UUID | None, file: UploadFile) -> School:
        school = await self.get_my_school(school_id)
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

        media_dir = Path(__file__).resolve().parents[3] / "media"
        logo_dir = media_dir / "school-logos"
        logo_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{school.id}{ext}"
        (logo_dir / filename).write_bytes(data)
        school.logo_url = f"/media/school-logos/{filename}"
        await self.db.flush()
        return school
