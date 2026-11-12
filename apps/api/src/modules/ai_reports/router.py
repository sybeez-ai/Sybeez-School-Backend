from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.ai_reports.schemas import AiReportOut, GenerateReportRequest
from src.modules.ai_reports.service import AiReportsService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/ai-reports", tags=["ai-reports"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


def _bearer(authorization: str | None = Header(default=None)) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:]
    return None


@router.get("/health")
@public
async def ai_reports_health():
    return {"module": "ai_reports", "status": "ok"}


@router.post("/generate", response_model=AiReportOut, status_code=201)
async def generate_report(
    body: GenerateReportRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
    bearer: Annotated[str | None, Depends(_bearer)] = None,
):
    """Generate a weekly AI report for a linked child and persist it."""
    return await AiReportsService(db).generate(
        school_id=_sid(user),
        parent_user_id=user.id,
        data=body,
        bearer_token=bearer,
    )


@router.get("/me", response_model=list[AiReportOut])
async def list_my_reports(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    """List all AI reports for the logged-in parent."""
    return await AiReportsService(db).list_for_parent(
        school_id=_sid(user),
        parent_user_id=user.id,
    )


@router.get("/{report_id}", response_model=AiReportOut)
async def get_report(
    report_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.PARENT))],
):
    return await AiReportsService(db).get(
        school_id=_sid(user),
        report_id=report_id,
        parent_user_id=user.id,
    )
