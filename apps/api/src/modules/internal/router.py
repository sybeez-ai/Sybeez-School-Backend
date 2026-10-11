from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import get_db
from src.core.exceptions import NotFoundError, UnauthorizedError
from src.core.rls import apply_rls
from src.db.models import SchoolMembership
from src.modules.access.deps import service_auth
from src.modules.internal.schemas import AIContextResponse
from src.modules.internal.service import InternalAIContextService
from src.shared.enums import MembershipStatus

router = APIRouter(prefix="/internal/v1", tags=["internal"])


async def require_internal_token(
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
) -> None:
    settings = get_settings()
    if not x_internal_token or x_internal_token not in settings.internal_service_tokens:
        raise UnauthorizedError("Invalid internal token")


@router.get("/students/ai-context", response_model=AIContextResponse, dependencies=[Depends(require_internal_token)])
@service_auth
async def student_ai_context(
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: UUID = Query(...),
    school_id: UUID = Query(...),
    subject_id: UUID | None = Query(None),
):
    membership = (
        await db.execute(
            select(SchoolMembership).where(
                SchoolMembership.user_id == user_id,
                SchoolMembership.school_id == school_id,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise NotFoundError("No active membership for this user in the school")
    await apply_rls(
        db,
        user_id=user_id,
        school_id=school_id,
        role=membership.role,
        membership_id=membership.id,
    )
    return await InternalAIContextService(db).build(user_id=user_id, school_id=school_id, subject_id=subject_id)
