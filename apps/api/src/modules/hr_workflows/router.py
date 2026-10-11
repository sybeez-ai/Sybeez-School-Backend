from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.hr.audit import SELF_SERVICE_ROLES
from src.modules.hr_workflows.schemas import (
    OwnerOut,
    TaskIn,
    TaskToggle,
    TaskUpdate,
    TemplateOut,
    WorkflowCancel,
    WorkflowCreate,
    WorkflowOut,
    WorkflowType,
    WorkflowUpdate,
)
from src.modules.hr_workflows.service import HrWorkflowService
from src.shared.enums import UserRole

router = APIRouter(prefix="/hr", tags=["hr-workflows"])

Admin = Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))]
Teacher = Annotated[CurrentUser, Depends(require_roles(*SELF_SERVICE_ROLES))]
Db = Annotated[AsyncSession, Depends(get_db)]


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


@router.get("/workflows", response_model=list[WorkflowOut])
async def list_workflows(
    user: Admin,
    db: Db,
    workflow_type: Annotated[
        str | None, Query(pattern="(?i)^(ONBOARDING|OFFBOARDING)$")
    ] = None,
    status: Annotated[
        str | None, Query(pattern="(?i)^(ACTIVE|OPEN|IN_PROGRESS|COMPLETED|CANCELLED)$")
    ] = None,
    employee_id: UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return await HrWorkflowService(db).list(
        _sid(user),
        workflow_type=workflow_type,
        status=status,
        employee_id=employee_id,
        q=q,
    )


@router.get("/workflows/owners", response_model=list[OwnerOut])
async def workflow_owners(user: Admin, db: Db):
    return await HrWorkflowService(db).owners(_sid(user))


@router.get("/workflows/template", response_model=TemplateOut)
async def workflow_template(user: Admin, db: Db, workflow_type: WorkflowType):
    return await HrWorkflowService(db).template(_sid(user), workflow_type)


@router.post("/workflows", response_model=WorkflowOut, status_code=201)
async def start_workflow(body: WorkflowCreate, user: Admin, db: Db):
    return await HrWorkflowService(db).start(_sid(user), user.id, body)


@router.get("/workflows/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(workflow_id: UUID, user: Admin, db: Db):
    return await HrWorkflowService(db).get(_sid(user), workflow_id)


@router.patch("/workflows/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(workflow_id: UUID, body: WorkflowUpdate, user: Admin, db: Db):
    return await HrWorkflowService(db).update(_sid(user), user.id, workflow_id, body)


@router.post("/workflows/{workflow_id}/complete", response_model=WorkflowOut)
async def complete_workflow(workflow_id: UUID, user: Admin, db: Db):
    return await HrWorkflowService(db).complete(_sid(user), user.id, workflow_id)


@router.post("/workflows/{workflow_id}/cancel", response_model=WorkflowOut)
async def cancel_workflow(workflow_id: UUID, body: WorkflowCancel, user: Admin, db: Db):
    return await HrWorkflowService(db).cancel(_sid(user), user.id, workflow_id, body)


@router.post(
    "/workflows/{workflow_id}/tasks", response_model=WorkflowOut, status_code=201
)
async def add_task(workflow_id: UUID, body: TaskIn, user: Admin, db: Db):
    return await HrWorkflowService(db).add_task(_sid(user), user.id, workflow_id, body)


@router.patch("/workflows/{workflow_id}/tasks/{task_id}", response_model=WorkflowOut)
async def update_task(
    workflow_id: UUID, task_id: UUID, body: TaskUpdate, user: Admin, db: Db
):
    return await HrWorkflowService(db).update_task(
        _sid(user), user.id, workflow_id, task_id, body
    )


@router.post(
    "/workflows/{workflow_id}/tasks/{task_id}/toggle", response_model=WorkflowOut
)
async def toggle_task(
    workflow_id: UUID, task_id: UUID, body: TaskToggle, user: Admin, db: Db
):
    return await HrWorkflowService(db).toggle_task(
        _sid(user), user.id, workflow_id, task_id, body
    )


@router.delete("/workflows/{workflow_id}/tasks/{task_id}", response_model=WorkflowOut)
async def delete_task(workflow_id: UUID, task_id: UUID, user: Admin, db: Db):
    return await HrWorkflowService(db).delete_task(
        _sid(user), user.id, workflow_id, task_id
    )


@router.get("/me/workflows", response_model=list[WorkflowOut])
async def my_workflows(user: Teacher, db: Db):
    return await HrWorkflowService(db).my_workflows(_sid(user), user.id)
