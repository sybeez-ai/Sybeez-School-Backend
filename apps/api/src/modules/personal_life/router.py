from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.personal_life.schemas import (
    GoalCreate,
    GoalOut,
    GoalUpdate,
    HabitCheckinOut,
    HabitCreate,
    HabitOut,
    HabitUpdate,
)
from src.modules.personal_life.service import PersonalLifeService
from src.shared.enums import UserRole
from src.modules.access.deps import public

router = APIRouter(prefix="/personal-life", tags=["personal-life"])

_ROLES = (UserRole.STUDENT, UserRole.TEACHER)


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/health")
@public
async def personal_life_health():
    return {"module": "personal_life", "status": "ok"}


# ── Goals ─────────────────────────────────────────────────────────────────────


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).list_goals(school_id=_sid(user), user_id=user.id)


@router.post("/goals", status_code=201, response_model=GoalOut)
async def create_goal(
    body: GoalCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).create_goal(
        school_id=_sid(user), user_id=user.id, data=body
    )


@router.patch("/goals/{goal_id}", response_model=GoalOut)
async def update_goal(
    goal_id: UUID,
    body: GoalUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).update_goal(
        school_id=_sid(user), user_id=user.id, goal_id=goal_id, data=body
    )


@router.delete("/goals/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    await PersonalLifeService(db).delete_goal(
        school_id=_sid(user), user_id=user.id, goal_id=goal_id
    )
    return None


@router.post("/goals/{goal_id}/milestones/{milestone_id}/toggle", response_model=GoalOut)
async def toggle_milestone(
    goal_id: UUID,
    milestone_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).toggle_milestone(
        school_id=_sid(user),
        user_id=user.id,
        goal_id=goal_id,
        milestone_id=milestone_id,
    )


# ── Habits ────────────────────────────────────────────────────────────────────


@router.get("/habits", response_model=list[HabitOut])
async def list_habits(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).list_habits(school_id=_sid(user), user_id=user.id)


@router.post("/habits", status_code=201, response_model=HabitOut)
async def create_habit(
    body: HabitCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).create_habit(
        school_id=_sid(user), user_id=user.id, data=body
    )


@router.patch("/habits/{habit_id}", response_model=HabitOut)
async def update_habit(
    habit_id: UUID,
    body: HabitUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    return await PersonalLifeService(db).update_habit(
        school_id=_sid(user), user_id=user.id, habit_id=habit_id, data=body
    )


@router.delete("/habits/{habit_id}", status_code=204)
async def delete_habit(
    habit_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
):
    await PersonalLifeService(db).delete_habit(
        school_id=_sid(user), user_id=user.id, habit_id=habit_id
    )
    return None


@router.post("/habits/{habit_id}/checkin", response_model=HabitCheckinOut)
async def toggle_habit_checkin(
    habit_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(*_ROLES))],
    on_date: date | None = Query(None),
):
    return await PersonalLifeService(db).toggle_checkin(
        school_id=_sid(user), user_id=user.id, habit_id=habit_id, on_date=on_date
    )
