"""Content library: school-owned notes, question banks, videos and links, tagged by board, grade and subject.

Admins and teachers manage items; students read. Parents and finance/HR roles get 403. Every change is audited.
Links must be http(s). File uploads are intentionally not here yet (they need virus scanning first).
"""

from typing import Annotated
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from src.db.models import ContentItem
from src.db.models.content_library import BOARDS, KINDS
from src.modules.access.audit import write_audit, write_scoped_audit
from src.shared.enums import UserRole

router = APIRouter(prefix="/content-library", tags=["content-library"])
DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER, UserRole.STUDENT))]
Writer = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))]


class ItemIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str
    board: str
    grade_id: UUID | None = None
    subject_id: UUID | None = None
    description: str | None = Field(default=None, max_length=2000)
    body: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        return v

    @field_validator("board")
    @classmethod
    def _board(cls, v: str) -> str:
        v = v.upper()
        if v not in BOARDS:
            raise ValueError(f"board must be one of {', '.join(BOARDS)}")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        p = urlparse(v)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValueError("url must start with http:// or https://")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for t in v:
            t = t.strip().lower()[:40]
            if t and t not in out:
                out.append(t)
        return out


class ItemOut(BaseModel):
    id: UUID
    title: str
    kind: str
    board: str
    grade_id: UUID | None
    subject_id: UUID | None
    description: str | None
    body: str | None
    url: str | None
    tags: list[str]


def _out(r: ContentItem) -> ItemOut:
    return ItemOut(id=r.id, title=r.title, kind=r.kind, board=r.board, grade_id=r.grade_id, subject_id=r.subject_id,
                   description=r.description, body=r.body, url=r.url, tags=list(r.tags or []))


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


async def _audit(db, user: CurrentUser, **kw) -> None:
    try:
        async with db.begin_nested():
            await write_scoped_audit(db, **kw)
    except Exception:
        await write_audit(db, school_id=_sid(user), actor_id=user.id, **kw)


async def _check_refs(db, school_id: UUID, body: ItemIn) -> None:
    from src.db.models import Grade, Subject

    if body.grade_id and (await db.execute(select(Grade.id).where(Grade.id == body.grade_id, Grade.school_id == school_id))).first() is None:
        raise ValidationError("Unknown grade")
    if body.subject_id and (await db.execute(select(Subject.id).where(Subject.id == body.subject_id, Subject.school_id == school_id))).first() is None:
        raise ValidationError("Unknown subject")
    if body.kind in ("VIDEO", "LINK") and not body.url:
        raise ValidationError("A link or video needs a url")
    if body.kind in ("NOTE", "QUESTION_BANK") and not (body.body or body.url):
        raise ValidationError("Add the text or a link")


@router.get("/boards")
async def boards(user: Reader):
    return {"boards": list(BOARDS), "kinds": list(KINDS)}


@router.get("", response_model=list[ItemOut])
async def list_items(
    db: DB, user: Reader,
    board: str | None = None, grade_id: UUID | None = None, subject_id: UUID | None = None,
    kind: str | None = None, tag: str | None = None, q: Annotated[str | None, Query(max_length=100)] = None,
):
    stmt = select(ContentItem).where(ContentItem.school_id == _sid(user))
    if board:
        stmt = stmt.where(ContentItem.board == board.upper())
    if grade_id:
        stmt = stmt.where(ContentItem.grade_id == grade_id)
    if subject_id:
        stmt = stmt.where(ContentItem.subject_id == subject_id)
    if kind:
        stmt = stmt.where(ContentItem.kind == kind)
    if tag:
        stmt = stmt.where(ContentItem.tags.contains([tag.strip().lower()]))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(ContentItem.title).like(like))
    rows = (await db.execute(stmt.order_by(ContentItem.created_at.desc()).limit(200))).scalars().all()
    return [_out(r) for r in rows]


@router.post("", status_code=201, response_model=ItemOut)
async def create_item(body: ItemIn, db: DB, user: Writer):
    sid = _sid(user)
    await _check_refs(db, sid, body)
    row = ContentItem(school_id=sid, created_by=user.id, **body.model_dump())
    db.add(row)
    await db.flush()
    await _audit(db, user, action="content.created", resource_type="content_item", resource_id=str(row.id),
                 message="Content added", details={"board": row.board, "kind": row.kind})
    return _out(row)


async def _get(db, user: CurrentUser, item_id: UUID) -> ContentItem:
    row = (await db.execute(select(ContentItem).where(ContentItem.id == item_id, ContentItem.school_id == _sid(user)))).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Content not found")
    return row


@router.put("/{item_id}", response_model=ItemOut)
async def update_item(item_id: UUID, body: ItemIn, db: DB, user: Writer):
    row = await _get(db, user, item_id)
    await _check_refs(db, _sid(user), body)
    for k, v in body.model_dump().items():
        setattr(row, k, v)
    await db.flush()
    await _audit(db, user, action="content.updated", resource_type="content_item", resource_id=str(item_id),
                 message="Content updated", details={"board": row.board, "kind": row.kind})
    return _out(row)


@router.delete("/{item_id}", status_code=204)
async def delete_item(item_id: UUID, db: DB, user: Writer):
    row = await _get(db, user, item_id)
    await db.delete(row)
    await _audit(db, user, action="content.deleted", resource_type="content_item", resource_id=str(item_id),
                 message="Content deleted", details={"board": row.board})
    return Response(status_code=204)
