"""Private on-disk storage for HR files.

Deliberately outside ``media/``, which is served publicly by the static mount: these bytes are
only ever returned through authorised download endpoints.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

STORAGE_ROOT = Path(__file__).resolve().parents[3] / "private_media" / "hr-files"


def _resolve(key: str) -> Path:
    root = STORAGE_ROOT.resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("Invalid storage key")
    return path


def save(school_id: UUID, data: bytes, ext: str) -> str:
    key = f"{school_id}/{uuid4().hex}{ext}"
    path = _resolve(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return key


def path_for(key: str) -> Path:
    return _resolve(key)


def remove(key: str) -> None:
    try:
        _resolve(key).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass
