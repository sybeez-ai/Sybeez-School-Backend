from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.access.deps import require_permission
from src.modules.learning_sessions.schemas import LearningSessionOut, LearningSessionStart
from src.modules.learning_sessions.service import LearningSessionService

router = APIRouter(prefix="/learning-sessions", tags=["learning_sessions"])


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("/health")
async def learning_sessions_health(
    _: Annotated[CurrentUser, Depends(require_permission("learning_sessions.read"))],
):
    return {"module": "learning_sessions", "status": "ok"}


@router.get("", response_model=list[LearningSessionOut])
async def list_learning_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("learning_sessions.read"))],
):
    return await LearningSessionService(db).list_visible(user, _sid(user))


@router.get("/me", response_model=list[LearningSessionOut])
async def my_learning_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("learning_sessions.read"))],
):
    return await LearningSessionService(db).list_visible(user, _sid(user))


@router.post("", response_model=LearningSessionOut, status_code=201)
async def start_learning_session(
    body: LearningSessionStart,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("ai.use"))],
):
    return await LearningSessionService(db).start(user, _sid(user), body)


@router.post("/{session_id}/end", response_model=LearningSessionOut)
async def end_learning_session(
    session_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_permission("ai.use"))],
):
    return await LearningSessionService(db).end(user, _sid(user), session_id)
