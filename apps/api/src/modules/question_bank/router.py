from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles, require_school_scope
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.question_bank.schemas import QuestionBankCreate, QuestionBankOut, QuestionBankUpdate
from src.modules.question_bank.service import QuestionBankService
from src.shared.enums import UserRole

router = APIRouter(prefix="/question-bank", tags=["question-bank"])

Writer = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN, UserRole.TEACHER))]
Reader = Annotated[CurrentUser, Depends(require_school_scope)]
DB = Annotated[AsyncSession, Depends(get_db)]


def _sid(user: CurrentUser) -> UUID:
    if user.school_id is None:
        raise ForbiddenError("School context required")
    return user.school_id


@router.get("", response_model=list[QuestionBankOut])
async def list_questions(
    db: DB,
    user: Reader,
    subject_id: UUID | None = Query(default=None),
    class_id: UUID | None = Query(default=None),
):
    return await QuestionBankService(db).list_items(
        _sid(user), subject_id=subject_id, class_id=class_id, user=user
    )


@router.post("", response_model=QuestionBankOut, status_code=status.HTTP_201_CREATED)
async def create_question(body: QuestionBankCreate, db: DB, user: Writer):
    return await QuestionBankService(db).create(_sid(user), user, body)


@router.patch("/{item_id}", response_model=QuestionBankOut)
async def update_question(item_id: UUID, body: QuestionBankUpdate, db: DB, user: Writer):
    return await QuestionBankService(db).update(_sid(user), user, item_id, body)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(item_id: UUID, db: DB, user: Writer):
    await QuestionBankService(db).delete(_sid(user), user, item_id)
