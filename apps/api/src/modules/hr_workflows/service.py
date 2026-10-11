"""Onboarding/offboarding workflows: one active journey per employee and type, checklist-driven."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AppError, ConflictError, NotFoundError
from src.db.models import HrWorkflow, HrWorkflowTask, SchoolMembership, User
from src.db.models.hr import WORKFLOW_ACTIVE_STATUSES
from src.modules.employees.lookup import (
    employee_briefs,
    employee_for_user,
    get_employee_brief,
    user_names,
)
from src.modules.hr.audit import hr_audit
from src.modules.hr_notifications.service import (
    ADMIN_LINKS,
    HR_ONBOARDING,
    HrNotifier,
)
from src.modules.hr_settings.service import load_rules
from src.modules.hr_workflows.schemas import (
    OwnerOut,
    TaskIn,
    TaskOut,
    TaskToggle,
    TaskUpdate,
    TemplateOut,
    WorkflowCancel,
    WorkflowCreate,
    WorkflowOut,
    WorkflowUpdate,
)
from src.shared.enums import MembershipStatus, UserRole

WORKFLOW_LABEL = {"ONBOARDING": "Onboarding", "OFFBOARDING": "Offboarding"}


class HrWorkflowService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # ── reads ──────────────────────────────────────────────────────────────

    async def template(self, school_id: UUID, workflow_type: str) -> TemplateOut:
        rules = await load_rules(self.db, school_id)
        return TemplateOut(workflow_type=workflow_type, tasks=list(rules.checklist(workflow_type)))

    async def owners(self, school_id: UUID) -> list[OwnerOut]:
        rows = await self.db.execute(
            select(User.id, User.full_name)
            .join(SchoolMembership, SchoolMembership.user_id == User.id)
            .where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.role == UserRole.SCHOOL_ADMIN.value,
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
                User.is_active.is_(True),
            )
            .distinct()
            .order_by(User.full_name)
        )
        return [OwnerOut(user_id=uid, full_name=name) for uid, name in rows.all()]

    async def list(
        self,
        school_id: UUID,
        *,
        workflow_type: str | None = None,
        status: str | None = None,
        employee_id: UUID | None = None,
        q: str | None = None,
    ) -> list[WorkflowOut]:
        stmt = select(HrWorkflow).where(HrWorkflow.school_id == school_id)
        if workflow_type:
            stmt = stmt.where(HrWorkflow.workflow_type == workflow_type.upper())
        if status:
            s = status.upper()
            stmt = stmt.where(
                HrWorkflow.status.in_(WORKFLOW_ACTIVE_STATUSES)
                if s == "ACTIVE"
                else HrWorkflow.status == s
            )
        if employee_id:
            stmt = stmt.where(HrWorkflow.employee_id == employee_id)
        rows = list(
            (
                await self.db.execute(
                    stmt.order_by(HrWorkflow.created_at.desc()).limit(500)
                )
            ).scalars()
        )
        out = await self._many_out(school_id, rows, with_tasks=False)
        if q:
            needle = q.strip().lower()
            out = [
                o
                for o in out
                if needle in o.employee_name.lower()
                or needle in (o.employee_code or "").lower()
                or needle in (o.notes or "").lower()
            ]
        return out

    async def get(self, school_id: UUID, workflow_id: UUID) -> WorkflowOut:
        wf = await self._get(school_id, workflow_id)
        return (await self._many_out(school_id, [wf], with_tasks=True))[0]

    async def my_workflows(self, school_id: UUID, user_id: UUID) -> list[WorkflowOut]:
        emp = await employee_for_user(self.db, school_id, user_id)
        rows = list(
            (
                await self.db.execute(
                    select(HrWorkflow)
                    .where(
                        HrWorkflow.school_id == school_id,
                        HrWorkflow.employee_id == emp.id,
                        HrWorkflow.status != "CANCELLED",
                    )
                    .order_by(HrWorkflow.created_at.desc())
                )
            ).scalars()
        )
        return await self._many_out(school_id, rows, with_tasks=True)

    # ── writes ─────────────────────────────────────────────────────────────

    async def start(
        self, school_id: UUID, user_id: UUID, data: WorkflowCreate
    ) -> WorkflowOut:
        emp = await get_employee_brief(self.db, school_id, data.employee_id)
        owner = data.owner_user_id or user_id
        await self._assert_owner(school_id, owner, acting_user=user_id)
        tasks = (
            data.tasks
            if data.tasks is not None
            else [TaskIn(title=t) for t in (await load_rules(self.db, school_id)).checklist(data.workflow_type)]
        )
        if not tasks:
            raise AppError("Add at least one checklist task", status_code=422)
        for t in tasks:
            await self._assert_assignee(
                school_id, t.assignee_user_id, emp.user_id, user_id
            )
        expected = data.expected_completion_date
        if (
            data.workflow_type == "ONBOARDING"
            and "expected_completion_date" not in data.model_fields_set
        ):
            target_days = (await load_rules(self.db, school_id)).onboarding_target_days
            if target_days > 0:
                expected = data.start_date + timedelta(days=target_days)
        wf = HrWorkflow(
            school_id=school_id,
            employee_id=emp.id,
            workflow_type=data.workflow_type,
            status="OPEN",
            start_date=data.start_date,
            expected_completion_date=expected,
            notes=data.notes or None,
            owner_user_id=owner,
            created_by=user_id,
            updated_by=user_id,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(wf)
                await self.db.flush()
        except IntegrityError as exc:
            raise ConflictError(
                f"{emp.full_name} already has an active {WORKFLOW_LABEL[data.workflow_type].lower()} workflow"
            ) from exc
        rows = [
            HrWorkflowTask(
                id=uuid4(),
                school_id=school_id,
                workflow_id=wf.id,
                title=t.title,
                description=t.description or None,
                sort_order=(i + 1) * 10,
                due_date=t.due_date,
                assignee_user_id=t.assignee_user_id,
                created_by=user_id,
            )
            for i, t in enumerate(tasks)
        ]
        self.db.add_all(rows)
        await self.db.flush()
        label = WORKFLOW_LABEL[wf.workflow_type]
        notifier = HrNotifier(self.db)
        await notifier.to_employee(
            school_id,
            emp.user_id,
            kind="workflow",
            category=HR_ONBOARDING,
            title=f"Your {label.lower()} has started",
            body=f"{len(tasks)} checklist item{'s' if len(tasks) != 1 else ''} — track progress in My HR.",
            entity_id=wf.id,
            event="started",
            actor=user_id,
        )
        await notifier.send(
            school_id,
            [owner],
            category=HR_ONBOARDING,
            title=f"You own the {label.lower()} of {emp.full_name}",
            body=f"Started {wf.start_date:%d %b %Y} with {len(tasks)} checklist items.",
            link=ADMIN_LINKS["workflow"],
            entity_type="hr_workflow",
            entity_id=wf.id,
            event=f"owner:{owner}",
            actor=user_id,
        )
        for t in rows:
            await self._notify_assignee(wf, emp.full_name, emp.user_id, t.id, t.title, t.assignee_user_id, user_id)
        await hr_audit(
            self.db,
            school_id=school_id,
            actor_id=user_id,
            action=f"hr.{wf.workflow_type.lower()}.started",
            resource_type="employee",
            resource_id=emp.id,
            message=f"{label} started for {emp.full_name}",
            details={"workflow_id": wf.id, "tasks": len(rows)},
        )
        return await self.get(school_id, wf.id)

    async def update(
        self, school_id: UUID, user_id: UUID, workflow_id: UUID, data: WorkflowUpdate
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "edited")
        payload = data.model_dump(exclude_unset=True)
        if "owner_user_id" in payload:
            if payload["owner_user_id"] is None:
                raise AppError("A workflow must have an owner", status_code=422)
            await self._assert_owner(
                school_id, payload["owner_user_id"], acting_user=user_id
            )
        if "start_date" in payload and payload["start_date"] is None:
            raise AppError("Start date is required", status_code=422)
        start = payload.get("start_date", wf.start_date)
        expected = payload.get("expected_completion_date", wf.expected_completion_date)
        if expected and expected < start:
            raise AppError(
                "Expected completion date cannot be before the start date",
                status_code=422,
            )
        if "notes" in payload:
            payload["notes"] = payload["notes"] or None
        for k, v in payload.items():
            setattr(wf, k, v)
        wf.updated_by = user_id
        await self.db.flush()
        return await self.get(school_id, wf.id)

    async def add_task(
        self, school_id: UUID, user_id: UUID, workflow_id: UUID, data: TaskIn
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "changed")
        emp = await get_employee_brief(self.db, school_id, wf.employee_id)
        await self._assert_assignee(
            school_id, data.assignee_user_id, emp.user_id, user_id
        )
        last = (
            await self.db.execute(
                select(func.max(HrWorkflowTask.sort_order)).where(
                    HrWorkflowTask.workflow_id == wf.id
                )
            )
        ).scalar_one_or_none()
        task = HrWorkflowTask(
            id=uuid4(),
            school_id=school_id,
            workflow_id=wf.id,
            title=data.title,
            description=data.description or None,
            sort_order=(last or 0) + 10,
            due_date=data.due_date,
            assignee_user_id=data.assignee_user_id,
            created_by=user_id,
        )
        self.db.add(task)
        await self._touch(wf, user_id)
        await self._notify_assignee(
            wf, emp.full_name, emp.user_id, task.id, data.title, data.assignee_user_id, user_id
        )
        return await self.get(school_id, wf.id)

    async def update_task(
        self,
        school_id: UUID,
        user_id: UUID,
        workflow_id: UUID,
        task_id: UUID,
        data: TaskUpdate,
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "changed")
        task = await self._get_task(school_id, wf.id, task_id)
        payload = data.model_dump(exclude_unset=True)
        if "title" in payload and not payload["title"]:
            raise AppError("Task title is required", status_code=422)
        if "sort_order" in payload and payload["sort_order"] is None:
            payload.pop("sort_order")
        new_assignee = payload.get("assignee_user_id")
        reassigned = bool(new_assignee and new_assignee != task.assignee_user_id)
        if new_assignee:
            emp = await get_employee_brief(self.db, school_id, wf.employee_id)
            await self._assert_assignee(
                school_id, new_assignee, emp.user_id, user_id
            )
        if "description" in payload:
            payload["description"] = payload["description"] or None
        for k, v in payload.items():
            setattr(task, k, v)
        await self._touch(wf, user_id)
        if reassigned:
            await self._notify_assignee(
                wf, emp.full_name, emp.user_id, task.id, task.title, new_assignee, user_id
            )
        return await self.get(school_id, wf.id)

    async def toggle_task(
        self,
        school_id: UUID,
        user_id: UUID,
        workflow_id: UUID,
        task_id: UUID,
        data: TaskToggle,
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "changed")
        task = await self._get_task(school_id, wf.id, task_id)
        newly_done = data.done and not task.is_done
        if task.is_done != data.done:
            task.is_done = data.done
            task.completed_at = datetime.now(UTC) if data.done else None
            task.completed_by = user_id if data.done else None
        await self.db.flush()
        await self._recompute_status(wf)
        await self._touch(wf, user_id)
        if newly_done:
            emp = await get_employee_brief(self.db, school_id, wf.employee_id)
            total, done = await self._counts(wf.id)
            await HrNotifier(self.db).send(
                school_id,
                [wf.owner_user_id],
                category=HR_ONBOARDING,
                title=f"Task completed: {task.title}",
                body=f"{WORKFLOW_LABEL[wf.workflow_type]} of {emp.full_name} — {done} of {total} done.",
                link=ADMIN_LINKS["workflow"],
                entity_type="hr_workflow",
                entity_id=wf.id,
                event=f"task:{task.id}:done",
                actor=user_id,
            )
        return await self.get(school_id, wf.id)

    async def delete_task(
        self, school_id: UUID, user_id: UUID, workflow_id: UUID, task_id: UUID
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "changed")
        task = await self._get_task(school_id, wf.id, task_id)
        remaining = (
            await self.db.execute(
                select(func.count()).where(HrWorkflowTask.workflow_id == wf.id)
            )
        ).scalar_one()
        if remaining <= 1:
            raise AppError(
                "A workflow needs at least one checklist task", status_code=422
            )
        await self.db.delete(task)
        await self.db.flush()
        await self._recompute_status(wf)
        await self._touch(wf, user_id)
        return await self.get(school_id, wf.id)

    async def complete(
        self, school_id: UUID, user_id: UUID, workflow_id: UUID
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "completed")
        total, done = await self._counts(wf.id)
        if total == 0 or done < total:
            raise AppError(
                f"Finish every checklist task before completing ({done} of {total} done)",
                status_code=422,
            )
        wf.status = "COMPLETED"
        wf.completed_at = datetime.now(UTC)
        wf.completed_by = user_id
        wf.updated_by = user_id
        await self.db.flush()
        await self._notify_closed(wf, user_id, "completed")
        return await self.get(school_id, wf.id)

    async def cancel(
        self, school_id: UUID, user_id: UUID, workflow_id: UUID, data: WorkflowCancel
    ) -> WorkflowOut:
        wf = await self._get_active(school_id, workflow_id, "cancelled")
        wf.status = "CANCELLED"
        wf.cancelled_at = datetime.now(UTC)
        wf.cancelled_by = user_id
        wf.cancel_reason = data.reason
        wf.updated_by = user_id
        await self.db.flush()
        await self._notify_closed(wf, user_id, "cancelled", data.reason)
        return await self.get(school_id, wf.id)

    # ── internals ──────────────────────────────────────────────────────────

    async def _notify_assignee(
        self,
        wf: HrWorkflow,
        employee_name: str,
        employee_user: UUID | None,
        task_id: UUID,
        task_title: str,
        assignee: UUID | None,
        actor: UUID,
    ) -> None:
        if not assignee:
            return
        label = WORKFLOW_LABEL[wf.workflow_type].lower()
        mine = assignee == employee_user
        notifier = HrNotifier(self.db)
        links = await notifier.employee_links(wf.school_id, assignee) if mine else ADMIN_LINKS
        await notifier.send(
            wf.school_id,
            [assignee],
            category=HR_ONBOARDING,
            title=f"Task assigned to you: {task_title}",
            body=f"Part of your {label}." if mine else f"Part of the {label} of {employee_name}.",
            link=links["workflow"],
            entity_type="hr_workflow",
            entity_id=wf.id,
            event=f"task:{task_id}:assigned:{assignee}",
            actor=actor,
        )

    async def _notify_closed(
        self, wf: HrWorkflow, actor: UUID, verb: str, reason: str | None = None
    ) -> None:
        emp = await get_employee_brief(self.db, wf.school_id, wf.employee_id)
        label = WORKFLOW_LABEL[wf.workflow_type]
        notifier = HrNotifier(self.db)
        if verb == "completed":
            await notifier.to_employee(
                wf.school_id,
                emp.user_id,
                kind="workflow",
                category=HR_ONBOARDING,
                title=f"Your {label.lower()} is complete",
                body="Every checklist item has been finished.",
                entity_id=wf.id,
                event="completed",
                actor=actor,
            )
        await notifier.send(
            wf.school_id,
            [wf.owner_user_id],
            category=HR_ONBOARDING,
            title=f"{label} of {emp.full_name} {verb}",
            body=reason or "Checklist closed.",
            link=ADMIN_LINKS["workflow"],
            entity_type="hr_workflow",
            entity_id=wf.id,
            event=f"closed:{verb}",
            actor=actor,
        )
        await hr_audit(
            self.db,
            school_id=wf.school_id,
            actor_id=actor,
            action=f"hr.{wf.workflow_type.lower()}.{verb}",
            resource_type="employee",
            resource_id=emp.id,
            message=f"{label} of {emp.full_name} {verb}",
            details={"workflow_id": wf.id, "reason": reason},
        )

    async def _assert_owner(
        self, school_id: UUID, owner: UUID, *, acting_user: UUID
    ) -> None:
        if owner == acting_user:
            return
        if owner not in {o.user_id for o in await self.owners(school_id)}:
            raise AppError("The owner must be an active school admin", status_code=422)

    async def _assert_assignee(
        self,
        school_id: UUID,
        assignee: UUID | None,
        employee_user: UUID | None,
        acting_user: UUID,
    ) -> None:
        if assignee is None or assignee in (acting_user, employee_user):
            return
        if assignee not in {o.user_id for o in await self.owners(school_id)}:
            raise AppError(
                "Tasks can be assigned to a school admin or to the employee",
                status_code=422,
            )

    async def _get(self, school_id: UUID, workflow_id: UUID) -> HrWorkflow:
        wf = (
            await self.db.execute(
                select(HrWorkflow)
                .where(HrWorkflow.id == workflow_id, HrWorkflow.school_id == school_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if wf is None:
            raise NotFoundError("Workflow not found")
        return wf

    async def _get_active(
        self, school_id: UUID, workflow_id: UUID, verb: str
    ) -> HrWorkflow:
        wf = await self._get(school_id, workflow_id)
        if wf.status not in WORKFLOW_ACTIVE_STATUSES:
            raise AppError(
                f"This workflow is {wf.status.lower()} and can no longer be {verb}",
                status_code=422,
            )
        return wf

    async def _get_task(
        self, school_id: UUID, workflow_id: UUID, task_id: UUID
    ) -> HrWorkflowTask:
        task = (
            await self.db.execute(
                select(HrWorkflowTask).where(
                    HrWorkflowTask.id == task_id,
                    HrWorkflowTask.workflow_id == workflow_id,
                    HrWorkflowTask.school_id == school_id,
                )
            )
        ).scalar_one_or_none()
        if task is None:
            raise NotFoundError("Task not found")
        return task

    async def _counts(self, workflow_id: UUID) -> tuple[int, int]:
        total, done = (
            await self.db.execute(
                select(
                    func.count(),
                    func.count().filter(HrWorkflowTask.is_done.is_(True)),
                ).where(HrWorkflowTask.workflow_id == workflow_id)
            )
        ).one()
        return int(total), int(done)

    async def _recompute_status(self, wf: HrWorkflow) -> None:
        _, done = await self._counts(wf.id)
        wf.status = "IN_PROGRESS" if done else "OPEN"

    async def _touch(self, wf: HrWorkflow, user_id: UUID) -> None:
        wf.updated_by = user_id
        wf.updated_at = datetime.now(UTC)
        await self.db.flush()

    async def _many_out(
        self, school_id: UUID, rows: list[HrWorkflow], *, with_tasks: bool
    ) -> list[WorkflowOut]:
        if not rows:
            return []
        ids = [r.id for r in rows]
        tasks = list(
            (
                await self.db.execute(
                    select(HrWorkflowTask)
                    .where(
                        HrWorkflowTask.workflow_id.in_(ids),
                        HrWorkflowTask.school_id == school_id,
                    )
                    .order_by(HrWorkflowTask.sort_order, HrWorkflowTask.created_at)
                    .execution_options(populate_existing=True)
                )
            ).scalars()
        )
        by_wf: dict[UUID, list[HrWorkflowTask]] = defaultdict(list)
        for t in tasks:
            by_wf[t.workflow_id].append(t)
        emps = await employee_briefs(self.db, school_id, {r.employee_id for r in rows})
        users = await user_names(
            self.db,
            {
                u
                for r in rows
                for u in (
                    r.completed_by,
                    r.cancelled_by,
                    r.owner_user_id,
                    r.created_by,
                    r.updated_by,
                )
            }
            | {u for t in tasks for u in (t.completed_by, t.assignee_user_id)},
        )
        today = date.today()

        def task_out(t: HrWorkflowTask) -> TaskOut:
            return TaskOut(
                id=t.id,
                title=t.title,
                description=t.description,
                sort_order=t.sort_order,
                is_done=t.is_done,
                completed_at=t.completed_at,
                completed_by_name=users.get(t.completed_by) if t.completed_by else None,
                due_date=t.due_date,
                is_overdue=bool(t.due_date and not t.is_done and t.due_date < today),
                assignee_user_id=t.assignee_user_id,
                assignee_name=users.get(t.assignee_user_id)
                if t.assignee_user_id
                else None,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )

        out: list[WorkflowOut] = []
        for r in rows:
            emp = emps.get(r.employee_id)
            items = [task_out(t) for t in by_wf.get(r.id, [])]
            active = r.status in WORKFLOW_ACTIVE_STATUSES
            out.append(
                WorkflowOut(
                    id=r.id,
                    employee_id=r.employee_id,
                    employee_name=emp.full_name if emp else "—",
                    employee_code=emp.employee_code if emp else None,
                    department_name=emp.department_name if emp else None,
                    designation_name=emp.designation_name if emp else None,
                    workflow_type=r.workflow_type,
                    status=r.status,
                    start_date=r.start_date,
                    expected_completion_date=r.expected_completion_date,
                    completed_at=r.completed_at,
                    completed_by_name=users.get(r.completed_by)
                    if r.completed_by
                    else None,
                    cancelled_at=r.cancelled_at,
                    cancelled_by_name=users.get(r.cancelled_by)
                    if r.cancelled_by
                    else None,
                    cancel_reason=r.cancel_reason,
                    notes=r.notes,
                    owner_user_id=r.owner_user_id,
                    owner_name=users.get(r.owner_user_id) if r.owner_user_id else None,
                    created_by_name=users.get(r.created_by) if r.created_by else None,
                    updated_by_name=users.get(r.updated_by) if r.updated_by else None,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                    total_tasks=len(items),
                    done_tasks=sum(1 for t in items if t.is_done),
                    overdue_tasks=sum(1 for t in items if t.is_overdue)
                    if active
                    else 0,
                    tasks=items if with_tasks else None,
                )
            )
        return out
