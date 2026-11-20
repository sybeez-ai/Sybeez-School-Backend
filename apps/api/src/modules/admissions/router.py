from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import AppError, ForbiddenError
from src.modules.admissions.form import honeypot_tripped, missing_required, public_fields
from src.modules.admissions.ratelimit import allow_public_enquiry
from src.modules.admissions.schemas import (
    AdmissionApplicationCreate,
    AdmissionApplicationOut,
    AdmissionApplicationUpdate,
    AdmissionEnquiryCreate,
    AdmissionEnquiryOut,
    AdmissionEnquiryUpdate,
    AdmissionFormOut,
    PublicAdmissionOut,
    PublicEnquiryIn,
    PublicEnquiryOut,
    AdmissionStatusChangeIn,
    AdmissionStatusHistoryOut,
    AdmitApplicationIn,
    ConvertEnquiryIn,
)
from src.modules.admissions.service import AdmissionsService
from src.modules.students.schemas import StudentOut
from src.shared.enums import UserRole
from src.modules.access.deps import public
from src.modules.fees.tenant import bind_tenant, set_rls_bypass

router = APIRouter(tags=["admissions"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/admissions/health")
@public
async def admissions_health():
    return {"module": "admissions", "status": "ok"}


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    if request.client is None:
        return ""
    return request.client.host


@router.get("/admissions/form", response_model=AdmissionFormOut)
async def get_admission_form(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    from src.db.models import School

    school = await db.get(School, _sid(user))
    if school is None:
        raise ForbiddenError("School context required")
    return await AdmissionsService(db).form_for(school)


@router.put("/admissions/form", response_model=AdmissionFormOut)
async def save_admission_form(
    body: AdmissionFormOut,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await AdmissionsService(db).save_form(_sid(user), body.model_dump())


@router.get("/admissions/public/{slug}", response_model=PublicAdmissionOut)
@public
async def public_admission_form(slug: str, db: Annotated[AsyncSession, Depends(get_db)]):
    service = AdmissionsService(db)
    await set_rls_bypass(db, True)
    school = await service.school_by_slug(slug)
    await set_rls_bypass(db, False)
    form = await service.form_for(school)
    return PublicAdmissionOut(school_name=school.name, intro=form["intro"], fields=public_fields(form))


@router.post("/admissions/public/{slug}", response_model=PublicEnquiryOut, status_code=201)
@public
async def public_admission_submit(
    slug: str,
    body: PublicEnquiryIn,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not await allow_public_enquiry(_client_ip(request), slug.strip().lower()):
        raise AppError("Please wait a few minutes and try again.", code="rate_limited", status_code=429)
    if honeypot_tripped(body.company_website):
        return PublicEnquiryOut(ok=True)
    service = AdmissionsService(db)
    await set_rls_bypass(db, True)
    school = await service.school_by_slug(slug)
    await set_rls_bypass(db, False)
    await bind_tenant(db, school.id, role="PUBLIC")
    form = await service.form_for(school)
    payload = body.model_dump()
    missing = missing_required(form, payload)
    if missing:
        raise AppError(f"Add {missing[0]}.", code="incomplete", status_code=422)
    email = (body.email or "").strip()
    if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
        raise AppError("Enter a valid email.", code="invalid_email", status_code=422)
    enabled = {field["key"] for field in form["fields"] if field["enabled"]}
    await service.create_enquiry(
        school.id,
        None,
        AdmissionEnquiryCreate(
            student_name=body.student_name.strip(),
            parent_name=body.parent_name if "parent_name" in enabled else None,
            phone=body.phone if "phone" in enabled else None,
            email=email or None if "email" in enabled else None,
            notes=body.notes if "notes" in enabled else None,
        ),
    )
    return PublicEnquiryOut(ok=True)


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

