from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.notes.schemas import NoteCreate, NoteOut, NoteUpdate
from src.modules.notes.service import NotesService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/notes", tags=["notes"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
@public
async def notes_health():
    return {"module": "notes", "status": "ok"}


@router.get("/", response_model=list[NoteOut])
async def list_notes(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await NotesService(db).list_notes(_sid(user), user.id)


@router.post("/", status_code=201, response_model=NoteOut)
async def create_note(
    body: NoteCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await NotesService(db).create_note(_sid(user), user.id, body)


@router.patch("/{note_id}", response_model=NoteOut)
async def update_note(
    note_id: UUID,
    body: NoteUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    return await NotesService(db).update_note(_sid(user), user.id, note_id, body)


@router.delete("/{note_id}", status_code=204)
async def delete_note(
    note_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.STUDENT, UserRole.TEACHER))],
):
    await NotesService(db).delete_note(_sid(user), user.id, note_id)
    return None
