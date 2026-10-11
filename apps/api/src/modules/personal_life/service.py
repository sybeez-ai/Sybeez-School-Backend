from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.exceptions import NotFoundError
from src.db.models import (
    PersonalGoal,
    PersonalGoalMilestone,
    PersonalHabit,
    PersonalHabitCheckin,
)
from src.modules.personal_life.schemas import (
    GoalCreate,
    GoalOut,
    GoalUpdate,
    HabitCheckinOut,
    HabitCreate,
    HabitOut,
    HabitUpdate,
    MilestoneOut,
)


def _goal_out(goal: PersonalGoal) -> GoalOut:
    return GoalOut(
        id=goal.id,
        title=goal.title,
        category=goal.category,  # type: ignore[arg-type]
        target_date=goal.target_date,
        status=goal.status,  # type: ignore[arg-type]
        progress_percent=goal.progress_percent,
        completed_on=goal.completed_on,
        milestones=[
            MilestoneOut(
                id=m.id,
                label=m.label,
                completed=m.completed,
                sort_order=m.sort_order,
            )
            for m in (goal.milestones or [])
        ],
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )


def _recompute_progress(goal: PersonalGoal) -> None:
    ms = goal.milestones or []
    if not ms:
        goal.progress_percent = 100 if goal.status == "completed" else 0
        return
    done = sum(1 for m in ms if m.completed)
    goal.progress_percent = round(done / len(ms) * 100)
    if goal.progress_percent >= 100:
        goal.status = "completed"
        if goal.completed_on is None:
            goal.completed_on = date.today()
    elif goal.status == "completed":
        goal.status = "active"
        goal.completed_on = None


class PersonalLifeService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Goals ─────────────────────────────────────────────────────────────────

    async def list_goals(self, *, school_id: UUID, user_id: UUID) -> list[GoalOut]:
        rows = (
            await self.db.execute(
                select(PersonalGoal)
                .options(selectinload(PersonalGoal.milestones))
                .where(
                    PersonalGoal.school_id == school_id,
                    PersonalGoal.user_id == user_id,
                    PersonalGoal.is_deleted.is_(False),
                )
                .order_by(PersonalGoal.created_at.desc())
            )
        ).scalars().all()
        return [_goal_out(g) for g in rows]

    async def create_goal(
        self, *, school_id: UUID, user_id: UUID, data: GoalCreate
    ) -> GoalOut:
        goal = PersonalGoal(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            title=data.title.strip(),
            category=data.category,
            target_date=data.target_date,
            status="active",
            progress_percent=0,
        )
        self.db.add(goal)
        await self.db.flush()
        for i, m in enumerate(data.milestones):
            self.db.add(
                PersonalGoalMilestone(
                    id=uuid4(),
                    school_id=school_id,
                    goal_id=goal.id,
                    label=m.label.strip(),
                    completed=m.completed,
                    sort_order=i,
                )
            )
        await self.db.flush()
        await self.db.refresh(goal, attribute_names=["milestones"])
        _recompute_progress(goal)
        await self.db.commit()
        goal = await self._get_goal(school_id, user_id, goal.id)
        return _goal_out(goal)

    async def _get_goal(self, school_id: UUID, user_id: UUID, goal_id: UUID) -> PersonalGoal:
        goal = (
            await self.db.execute(
                select(PersonalGoal)
                .options(selectinload(PersonalGoal.milestones))
                .where(
                    PersonalGoal.id == goal_id,
                    PersonalGoal.school_id == school_id,
                    PersonalGoal.user_id == user_id,
                    PersonalGoal.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if not goal:
            raise NotFoundError("Goal not found")
        return goal

    async def update_goal(
        self, *, school_id: UUID, user_id: UUID, goal_id: UUID, data: GoalUpdate
    ) -> GoalOut:
        goal = await self._get_goal(school_id, user_id, goal_id)
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and payload["title"] is not None:
            goal.title = payload["title"].strip()
        if "category" in payload and payload["category"] is not None:
            goal.category = payload["category"]
        if "target_date" in payload:
            goal.target_date = payload["target_date"]
        if "status" in payload and payload["status"] is not None:
            goal.status = payload["status"]
            if payload["status"] == "completed":
                goal.completed_on = goal.completed_on or date.today()
                goal.progress_percent = 100
            else:
                goal.completed_on = None
        if "milestones" in payload and payload["milestones"] is not None:
            await self.db.execute(
                delete(PersonalGoalMilestone).where(PersonalGoalMilestone.goal_id == goal.id)
            )
            await self.db.flush()
            for i, m in enumerate(payload["milestones"]):
                self.db.add(
                    PersonalGoalMilestone(
                        id=uuid4(),
                        school_id=school_id,
                        goal_id=goal.id,
                        label=m["label"].strip() if isinstance(m, dict) else m.label.strip(),
                        completed=m["completed"] if isinstance(m, dict) else m.completed,
                        sort_order=i,
                    )
                )
            await self.db.flush()
            await self.db.refresh(goal, attribute_names=["milestones"])
            _recompute_progress(goal)
        await self.db.commit()
        goal = await self._get_goal(school_id, user_id, goal_id)
        return _goal_out(goal)

    async def delete_goal(self, *, school_id: UUID, user_id: UUID, goal_id: UUID) -> None:
        goal = await self._get_goal(school_id, user_id, goal_id)
        goal.is_deleted = True
        await self.db.commit()

    async def toggle_milestone(
        self, *, school_id: UUID, user_id: UUID, goal_id: UUID, milestone_id: UUID
    ) -> GoalOut:
        goal = await self._get_goal(school_id, user_id, goal_id)
        milestone = next((m for m in goal.milestones if m.id == milestone_id), None)
        if not milestone:
            raise NotFoundError("Milestone not found")
        milestone.completed = not milestone.completed
        _recompute_progress(goal)
        await self.db.commit()
        goal = await self._get_goal(school_id, user_id, goal_id)
        return _goal_out(goal)

    # ── Habits ────────────────────────────────────────────────────────────────

    async def list_habits(self, *, school_id: UUID, user_id: UUID) -> list[HabitOut]:
        habits = (
            await self.db.execute(
                select(PersonalHabit)
                .where(
                    PersonalHabit.school_id == school_id,
                    PersonalHabit.user_id == user_id,
                    PersonalHabit.is_deleted.is_(False),
                )
                .order_by(PersonalHabit.created_at.desc())
            )
        ).scalars().all()
        out: list[HabitOut] = []
        for h in habits:
            out.append(await self._habit_out(h, user_id))
        return out

    async def create_habit(
        self, *, school_id: UUID, user_id: UUID, data: HabitCreate
    ) -> HabitOut:
        habit = PersonalHabit(
            id=uuid4(),
            school_id=school_id,
            user_id=user_id,
            title=data.title.strip(),
            description=(data.description or "").strip() or None,
            frequency=data.frequency,
            weekly_target=data.weekly_target,
            is_active=True,
        )
        self.db.add(habit)
        await self.db.commit()
        await self.db.refresh(habit)
        return await self._habit_out(habit, user_id)

    async def _get_habit(self, school_id: UUID, user_id: UUID, habit_id: UUID) -> PersonalHabit:
        habit = (
            await self.db.execute(
                select(PersonalHabit).where(
                    PersonalHabit.id == habit_id,
                    PersonalHabit.school_id == school_id,
                    PersonalHabit.user_id == user_id,
                    PersonalHabit.is_deleted.is_(False),
                )
            )
        ).scalar_one_or_none()
        if not habit:
            raise NotFoundError("Habit not found")
        return habit

    async def update_habit(
        self, *, school_id: UUID, user_id: UUID, habit_id: UUID, data: HabitUpdate
    ) -> HabitOut:
        habit = await self._get_habit(school_id, user_id, habit_id)
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and payload["title"] is not None:
            habit.title = payload["title"].strip()
        if "description" in payload:
            habit.description = (payload["description"] or "").strip() or None
        if "frequency" in payload and payload["frequency"] is not None:
            habit.frequency = payload["frequency"]
        if "weekly_target" in payload and payload["weekly_target"] is not None:
            habit.weekly_target = payload["weekly_target"]
        if "is_active" in payload and payload["is_active"] is not None:
            habit.is_active = payload["is_active"]
        await self.db.commit()
        await self.db.refresh(habit)
        return await self._habit_out(habit, user_id)

    async def delete_habit(self, *, school_id: UUID, user_id: UUID, habit_id: UUID) -> None:
        habit = await self._get_habit(school_id, user_id, habit_id)
        habit.is_deleted = True
        await self.db.commit()

    async def toggle_checkin(
        self, *, school_id: UUID, user_id: UUID, habit_id: UUID, on_date: date | None = None
    ) -> HabitCheckinOut:
        habit = await self._get_habit(school_id, user_id, habit_id)
        day = on_date or date.today()
        existing = (
            await self.db.execute(
                select(PersonalHabitCheckin).where(
                    PersonalHabitCheckin.habit_id == habit.id,
                    PersonalHabitCheckin.checkin_date == day,
                )
            )
        ).scalar_one_or_none()
        checked = False
        if existing:
            await self.db.delete(existing)
            checked = False
        else:
            self.db.add(
                PersonalHabitCheckin(
                    id=uuid4(),
                    school_id=school_id,
                    habit_id=habit.id,
                    user_id=user_id,
                    checkin_date=day,
                )
            )
            checked = True
        await self.db.commit()
        stats = await self._habit_stats(habit.id, user_id)
        return HabitCheckinOut(
            habit_id=habit.id,
            checkin_date=day,
            checked=checked,
            streak_days=stats["streak"],
            weekly_done=stats["weekly_done"],
        )

    async def _habit_stats(self, habit_id: UUID, user_id: UUID) -> dict:
        today = date.today()
        week_start = today - timedelta(days=today.weekday())
        rows = (
            await self.db.execute(
                select(PersonalHabitCheckin.checkin_date)
                .where(
                    PersonalHabitCheckin.habit_id == habit_id,
                    PersonalHabitCheckin.user_id == user_id,
                    PersonalHabitCheckin.checkin_date >= today - timedelta(days=400),
                )
                .order_by(PersonalHabitCheckin.checkin_date.desc())
            )
        ).scalars().all()
        day_set = {d for d in rows}
        streak = 0
        for i in range(400):
            d = today - timedelta(days=i)
            if d not in day_set:
                break
            streak += 1
        # Best streak
        best = 0
        cur = 0
        for i in range(400, -1, -1):
            d = today - timedelta(days=i)
            if d in day_set:
                cur += 1
                best = max(best, cur)
            else:
                cur = 0
        weekly_done = sum(1 for d in day_set if d >= week_start)
        return {"streak": streak, "best": best, "weekly_done": weekly_done}

    async def _habit_out(self, habit: PersonalHabit, user_id: UUID) -> HabitOut:
        stats = await self._habit_stats(habit.id, user_id)
        today = date.today()
        checked_today = (
            await self.db.execute(
                select(func.count())
                .select_from(PersonalHabitCheckin)
                .where(
                    PersonalHabitCheckin.habit_id == habit.id,
                    PersonalHabitCheckin.checkin_date == today,
                )
            )
        ).scalar() or 0
        return HabitOut(
            id=habit.id,
            title=habit.title,
            description=habit.description,
            frequency=habit.frequency,  # type: ignore[arg-type]
            weekly_target=habit.weekly_target,
            streak_days=stats["streak"],
            best_streak=stats["best"],
            weekly_done=stats["weekly_done"],
            checked_today=bool(checked_today),
            is_active=habit.is_active,
            created_at=habit.created_at,
            updated_at=habit.updated_at,
        )
