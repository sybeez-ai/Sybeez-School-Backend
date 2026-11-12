"""Public school logo, so <img> tags can load it without a bearer token."""

from uuid import UUID

from fastapi import APIRouter, Response

from src.core.exceptions import NotFoundError
from src.core.storage import read_object
from src.modules.access.deps import public

router = APIRouter(prefix="/public", tags=["public"])

_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}


@router.get("/school-logos/{school_id}")
@public
async def school_logo(school_id: UUID) -> Response:
    for ext, media_type in _TYPES.items():
        try:
            data = read_object(f"school-logos/{school_id}{ext}")
        except (FileNotFoundError, ValueError):
            continue
        return Response(
            content=data,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
        )
    raise NotFoundError("Logo not found")
