from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.modules.hr.access import require_hr_admin
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr_requests.schemas import (
    AdminRequestCreate,
    MyProfileOut,
    RequestComment,
    RequestCreate,
    RequestDecision,
    RequestOut,
)
from src.modules.hr_requests.service import HrRequestService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr-requests"])

Admin = Annotated[CurrentUser, Depends(require_hr_admin)]
Teacher = Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/requests", response_model=list[RequestOut])
async def list_requests(
    user: Admin,
    db: Db,
    status: Annotated[
        str | None,
        Query(
            pattern="(?i)^(OPEN|PENDING|IN_REVIEW|APPROVED|REJECTED|RESOLVED|CANCELLED)$"
        ),
    ] = None,
    request_type: Annotated[
        str | None,
        Query(
            pattern="(?i)^(PROFILE_UPDATE|DOCUMENT_REQUEST|LETTER_REQUEST|HR_QUERY|OTHER)$"
        ),
    ] = None,
    employee_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return await HrRequestService(db).list(
        _sid(user),
        status=status,
        request_type=request_type,
        employee_id=employee_id,
        q=q,
    )


@router.post("/requests", response_model=RequestOut, status_code=201)
async def submit_for_employee(body: AdminRequestCreate, user: Admin, db: Db):
    return await HrRequestService(db).submit_for(_sid(user), user.id, body)


@router.get("/requests/{request_id}", response_model=RequestOut)
async def get_request(request_id: UUID, user: Admin, db: Db):
    return await HrRequestService(db).get(_sid(user), request_id)


@router.post("/requests/{request_id}/decision", response_model=RequestOut)
async def decide_request(request_id: UUID, body: RequestDecision, user: Admin, db: Db):
    return await HrRequestService(db).decide(_sid(user), user.id, request_id, body)


@router.post(
    "/requests/{request_id}/comments", response_model=RequestOut, status_code=201
)
async def comment_request(request_id: UUID, body: RequestComment, user: Admin, db: Db):
    return await HrRequestService(db).comment(_sid(user), user.id, request_id, body)


@router.get("/me/profile", response_model=MyProfileOut)
async def my_profile(user: Teacher, db: Db):
    return await HrRequestService(db).my_profile(_sid(user), user.id)


@router.get("/me/requests", response_model=list[RequestOut])
async def my_requests(user: Teacher, db: Db):
    return await HrRequestService(db).my_requests(_sid(user), user.id)


@router.post("/me/requests", response_model=RequestOut, status_code=201)
async def submit_my_request(body: RequestCreate, user: Teacher, db: Db):
    return await HrRequestService(db).submit_own(_sid(user), user.id, body)


@router.get("/me/requests/{request_id}", response_model=RequestOut)
async def get_my_request(request_id: UUID, user: Teacher, db: Db):
    return await HrRequestService(db).my_request(_sid(user), user.id, request_id)


@router.post("/me/requests/{request_id}/cancel", response_model=RequestOut)
async def cancel_my_request(request_id: UUID, user: Teacher, db: Db):
    return await HrRequestService(db).cancel_own(_sid(user), user.id, request_id)


@router.post(
    "/me/requests/{request_id}/comments", response_model=RequestOut, status_code=201
)
async def comment_my_request(
    request_id: UUID, body: RequestComment, user: Teacher, db: Db
):
    return await HrRequestService(db).comment_own(_sid(user), user.id, request_id, body)
