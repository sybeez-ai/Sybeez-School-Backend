from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.parent_messages.schemas import (
    GenerateRequest,
    InboxOut,
    PreferenceOut,
    PreferenceUpdate,
    ProgressUpdateCreate,
    ProgressUpdateOut,
    TranslationEdit,
    TranslationOut,
    TranslationSetOut,
)
from src.modules.parent_messages.service import ParentMessageService

router = APIRouter(prefix="/parent-messages", tags=["parent-messages"])

DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[CurrentUser, Depends(require_permission("announcements.read"))]
Writer = Annotated[CurrentUser, Depends(require_permission("announcements.write"))]


def _school(user: CurrentUser):
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/preferences/me", response_model=PreferenceOut)
async def my_preferences(db: DB, user: Reader):
    return await ParentMessageService(db).get_preferences(user, _school(user))


@router.put("/preferences/me", response_model=PreferenceOut)
async def update_my_preferences(body: PreferenceUpdate, request: Request, db: DB, user: Reader):
    return await ParentMessageService(db).update_preferences(user, _school(user), body, _ip(request))


@router.get("/inbox", response_model=InboxOut)
async def inbox(db: DB, user: Reader, limit: int = 30):
    return await ParentMessageService(db).inbox(user, _school(user), max(1, min(limit, 50)))


@router.get("/translations/{source_type}/{source_id}", response_model=TranslationSetOut)
async def get_translations(source_type: str, source_id: UUID, db: DB, user: Writer):
    return await ParentMessageService(db).translation_set(user, _school(user), source_type, source_id)


@router.post("/translations/{source_type}/{source_id}/generate", response_model=TranslationSetOut)
async def generate_translations(
    source_type: str, source_id: UUID, body: GenerateRequest, request: Request, db: DB, user: Writer
):
    return await ParentMessageService(db).generate(
        user, _school(user), source_type, source_id, body.languages, _ip(request)
    )


@router.put("/translations/{source_type}/{source_id}/{language}", response_model=TranslationOut)
async def edit_translation(
    source_type: str, source_id: UUID, language: str, body: TranslationEdit, request: Request, db: DB, user: Writer
):
    return await ParentMessageService(db).edit(
        user, _school(user), source_type, source_id, language, body.title, body.body, _ip(request)
    )


@router.post("/progress-updates", response_model=ProgressUpdateOut, status_code=201)
async def create_progress_update(body: ProgressUpdateCreate, request: Request, db: DB, user: Writer):
    return await ParentMessageService(db).create_progress_update(user, _school(user), body, _ip(request))


@router.get("/progress-updates", response_model=list[ProgressUpdateOut])
async def list_progress_updates(student_id: UUID, db: DB, user: Writer):
    return await ParentMessageService(db).list_progress_updates(user, _school(user), student_id)
