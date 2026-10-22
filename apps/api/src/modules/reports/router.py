from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.access.deps import require_permission
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.reports.schemas import ReportGenerateIn, SavedReportCreate, SavedReportUpdate
from src.modules.reports.service import ReportService
from src.shared.enums import UserRole

router = APIRouter(prefix="/reports", tags=["reports"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
async def reports_health(_: Annotated[CurrentUser, Depends(require_permission("reports.read"))]):
    return {"module": "reports", "status": "ok"}


@router.get("")
async def list_report_types(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER)),
    ],
):
    return await ReportService(db).catalog()


@router.post("/generate")
async def generate_report(
    body: ReportGenerateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ReportService(db).generate(_sid(user), user.id, body)


@router.post("/export/csv")
async def export_csv(
    body: ReportGenerateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    filename, content = await ReportService(db).export_csv(_sid(user), user.id, body)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/pdf")
async def export_pdf(
    body: ReportGenerateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    filename, content = await ReportService(db).export_pdf(_sid(user), user.id, body)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/saved")
async def list_saved(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ReportService(db).list_saved(_sid(user))


@router.post("/saved", status_code=201)
async def create_saved(
    body: SavedReportCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ReportService(db).create_saved(_sid(user), user.id, body)


@router.patch("/saved/{report_id}")
async def update_saved(
    report_id: UUID,
    body: SavedReportUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await ReportService(db).update_saved(_sid(user), report_id, body)


@router.delete("/saved/{report_id}", status_code=204)
async def delete_saved(
    report_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await ReportService(db).delete_saved(_sid(user), user.id, report_id)
