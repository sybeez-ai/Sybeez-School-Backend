from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import UnauthorizedError
from src.modules.internal.schemas import AIContextResponse
from src.modules.internal.service import InternalAIContextService

router = APIRouter(prefix="/internal/v1", tags=["internal"])


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not x_internal_token or x_internal_token not in settings.internal_service_tokens:
        raise UnauthorizedError("Invalid internal token")


@router.get("/students/ai-context", response_model=AIContextResponse, dependencies=[Depends(require_internal_token)])
async def student_ai_context(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: UUID = Query(...),
    school_id: UUID = Query(...),
    subject_id: UUID | None = Query(None),
):
    return await InternalAIContextService(db).build(user_id=user_id, school_id=school_id, subject_id=subject_id)
