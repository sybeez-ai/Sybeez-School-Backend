"""School logo bytes go to the existing object store under school-logos/{id}.{ext}."""

from __future__ import annotations

from uuid import UUID

from src.core.exceptions import ConflictError

LOGO_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".svg")
LOGO_MAX_BYTES = 2_000_000


def logo_payload(content_type: str | None, data: bytes) -> tuple[str, str]:
    media = (content_type or "").split(";", 1)[0].strip().lower()
    ext = LOGO_TYPES.get(media)
    if ext is None:
        raise ConflictError("Logo must be PNG, JPG, SVG, or WebP")
    if not data:
        raise ConflictError("Empty upload")
    if len(data) > LOGO_MAX_BYTES:
        raise ConflictError("Logo too large (max 2MB)")
    if ext == ".svg":
        lowered = data.lower()
        if b"<script" in lowered or b"javascript:" in lowered or b"onload=" in lowered:
            raise ConflictError("That SVG is not allowed")
    return media if media != "image/jpg" else "image/jpeg", ext


def store_school_logo(school_id: UUID, data: bytes, content_type: str, ext: str) -> str:
    from src.core.storage import delete_object, put_object

    key = f"school-logos/{school_id}{ext}"
    for old in LOGO_EXTENSIONS:
        if old != ext:
            delete_object(f"school-logos/{school_id}{old}")
    return put_object(key=key, data=data, content_type=content_type)


def clear_school_logo(school_id: UUID) -> None:
    from src.core.storage import delete_object

    for ext in LOGO_EXTENSIONS:
        delete_object(f"school-logos/{school_id}{ext}")
