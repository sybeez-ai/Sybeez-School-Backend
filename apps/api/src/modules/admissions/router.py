from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.admissions.schemas import (
    AdmissionApplicationCreate,
    AdmissionApplicationOut,
    AdmissionApplicationUpdate,
    AdmissionEnquiryCreate,
    AdmissionEnquiryOut,
    AdmissionEnquiryUpdate,
    AdmissionStatusChangeIn,
    AdmissionStatusHistoryOut,
    AdmitApplicationIn,
    ConvertEnquiryIn,
)
from src.modules.admissions.service import AdmissionsService
from src.modules.students.schemas import StudentOut
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(tags=["admissions"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/admissions/health")
@public
async def admissions_health():
    return {"module": "admissions", "status": "ok"}


# ── Enquiries ────────────────────────────────────────────────


@router.get("/admissions/enquiries", response_model=list[AdmissionEnquiryOut])
async def list_enquiries(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await AdmissionsService(db).list_enquiries(_sid(user), q=q, status=status, limit=limit, offset=offset)


@router.post("/admissions/enquiries", response_model=AdmissionEnquiryOut, status_code=201)
async def create_enquiry(
    body: AdmissionEnquiryCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).create_enquiry(_sid(user), user.id, body)


@router.get("/admissions/enquiries/{enquiry_id}", response_model=AdmissionEnquiryOut)
async def get_enquiry(
    enquiry_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).get_enquiry(_sid(user), enquiry_id)


@router.patch("/admissions/enquiries/{enquiry_id}", response_model=AdmissionEnquiryOut)
async def update_enquiry(
    enquiry_id: UUID,
    body: AdmissionEnquiryUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).update_enquiry(_sid(user), enquiry_id, body)


@router.post("/admissions/enquiries/{enquiry_id}/convert", response_model=AdmissionApplicationOut, status_code=201)
async def convert_enquiry(
    enquiry_id: UUID,
    body: ConvertEnquiryIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).convert_enquiry_to_application(_sid(user), enquiry_id, user.id, body)


# ── Applications ─────────────────────────────────────────────


@router.get("/admissions/applications", response_model=list[AdmissionApplicationOut])
async def list_applications(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    return await AdmissionsService(db).list_applications(_sid(user), q=q, status=status, limit=limit, offset=offset)


@router.post("/admissions/applications", response_model=AdmissionApplicationOut, status_code=201)
async def create_application(
    body: AdmissionApplicationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).create_application(_sid(user), user.id, body)


@router.get("/admissions/applications/{application_id}", response_model=AdmissionApplicationOut)
async def get_application(
    application_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).get_application(_sid(user), application_id)


@router.patch("/admissions/applications/{application_id}", response_model=AdmissionApplicationOut)
async def update_application(
    application_id: UUID,
    body: AdmissionApplicationUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).update_application(_sid(user), application_id, body)


@router.post("/admissions/applications/{application_id}/status", response_model=AdmissionApplicationOut)
async def change_application_status(
    application_id: UUID,
    body: AdmissionStatusChangeIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).change_status(_sid(user), application_id, user.id, status=body.status, note=body.note)


@router.get("/admissions/applications/{application_id}/history", response_model=list[AdmissionStatusHistoryOut])
async def list_application_history(
    application_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    rows = await AdmissionsService(db).list_status_history(_sid(user), application_id)
    return [AdmissionStatusHistoryOut.model_validate(r) for r in rows]


@router.post("/admissions/applications/{application_id}/admit", response_model=StudentOut, status_code=201)
async def admit_application(
    application_id: UUID,
    body: AdmitApplicationIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    _, student = await AdmissionsService(db).admit(_sid(user), application_id, user.id, body)
    return student

