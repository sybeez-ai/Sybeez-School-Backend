from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from src.db.models import School


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
