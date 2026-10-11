from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import CurrentUser, require_roles
from src.core.database import get_db
from src.core.exceptions import ForbiddenError
from src.modules.communication.schemas import (
    AnnouncementCreate,
    AnnouncementOut,
    AnnouncementUpdate,
    CommunicationStatsOut,
    ConversationCreate,
    ConversationOut,
    MessageContactOut,
    MessageCreate,
    MessageOut,
    NotificationDeliveryOut,
    NotificationListOut,
    NotificationOut,
    PreferencesOut,
    PreferencesUpdate,
    UnreadCountOut,
)
from src.modules.communication.service import CommunicationService, NotificationScope
from src.modules.hr_notifications.service import HR_ENTITY_TYPES
from src.shared.enums import UserRole
from src.modules.access.deps import public

CAMPUS_ROLES = (
    UserRole.STUDENT,
    UserRole.PARENT,
    UserRole.TEACHER,
    UserRole.STAFF,
    UserRole.SCHOOL_ADMIN,
)
# Release inbox roles, plus non-teaching STAFF and the HR membership role.
INBOX_ROLES = CAMPUS_ROLES + (
    UserRole.HR,
    UserRole.HR_MANAGER,
    UserRole.PAYROLL_ADMIN,
    UserRole.PAYROLL_APPROVER,
    UserRole.ACCOUNTANT,
    UserRole.CASHIER,
    UserRole.AUDITOR,
    UserRole.FINANCE,
)

router = APIRouter(prefix="/communication", tags=["communication"])


def _sid(u: CurrentUser) -> UUID:
    if u.school_id is None:
        raise ForbiddenError("School context required")
    return u.school_id


def _scope(u: CurrentUser, scope: str | None) -> NotificationScope | None:
    if scope != "hr":
        return None
    return _sid(u), HR_ENTITY_TYPES


@router.get("/health")
@public
async def communication_health():
    return {"module": "communication", "status": "ok"}


# ── notifications ────────────────────────────────────────────


@router.get("/notifications", response_model=NotificationListOut)
async def list_notifications(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
    unread: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    scope: Literal["hr"] | None = Query(None),
):
    return await CommunicationService(db).list_notifications(
        user.id, unread=unread, page=page, page_size=page_size, scope=_scope(user, scope)
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
async def unread_count(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
    scope: Literal["hr"] | None = Query(None),
):
    return await CommunicationService(db).unread_count(user.id, _scope(user, scope))


@router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
async def mark_notification_read(
    notification_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
):
    return await CommunicationService(db).mark_read(user.id, notification_id)


@router.post("/notifications/read-all", response_model=UnreadCountOut)
async def mark_all_read(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
    scope: Literal["hr"] | None = Query(None),
):
    return await CommunicationService(db).mark_all_read(user.id, _scope(user, scope))


@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
):
    await CommunicationService(db).delete_notification(user.id, notification_id)


# ── preferences ──────────────────────────────────────────────


@router.get("/preferences", response_model=PreferencesOut)
async def get_preferences(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
):
    return await CommunicationService(db).get_preferences(user.id, user.school_id)


@router.patch("/preferences", response_model=PreferencesOut)
async def update_preferences(
    body: PreferencesUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(require_roles(*INBOX_ROLES)),
    ],
):
    return await CommunicationService(db).update_preferences(user.id, user.school_id, body)


# ── deliveries (admin read-only log) ─────────────────────────


@router.get("/deliveries", response_model=list[NotificationDeliveryOut])
async def list_deliveries(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
    limit: int = Query(200, ge=1, le=500),
):
    return await CommunicationService(db).list_deliveries(_sid(user), limit=limit)


# ── announcements ────────────────────────────────────────────


@router.get("/announcements", response_model=list[AnnouncementOut])
async def list_announcements(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
    status: str | None = Query(None),
):
    return await CommunicationService(db).list_announcements(
        _sid(user),
        user_id=user.id,
        role=user.role.value,
        status=status,
    )


@router.post("/announcements", response_model=AnnouncementOut, status_code=201)
async def create_announcement(
    body: AnnouncementCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await CommunicationService(db).create_announcement(_sid(user), user.id, body)


class TeacherAnnouncementCreate(BaseModel):
    class_id: UUID
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    audience_role: str = "BOTH"
    priority: str = "NORMAL"


@router.post("/announcements/teacher", response_model=AnnouncementOut, status_code=201)
async def create_teacher_announcement(
    body: TeacherAnnouncementCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.TEACHER))],
):
    return await CommunicationService(db).create_teacher_announcement(
        _sid(user),
        user.id,
        class_id=body.class_id,
        title=body.title,
        content=body.content,
        audience_role=body.audience_role,
        priority=body.priority,
    )


@router.get("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def get_announcement(
    announcement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).get_announcement(
        _sid(user), announcement_id, viewer_id=user.id, viewer_role=user.role.value
    )


@router.patch("/announcements/{announcement_id}", response_model=AnnouncementOut)
async def update_announcement(
    announcement_id: UUID,
    body: AnnouncementUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await CommunicationService(db).update_announcement(
        _sid(user), user.id, announcement_id, body
    )


@router.post("/announcements/{announcement_id}/publish", response_model=AnnouncementOut)
async def publish_announcement(
    announcement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await CommunicationService(db).publish_announcement(
        _sid(user), user.id, announcement_id
    )


@router.post("/announcements/{announcement_id}/archive", response_model=AnnouncementOut)
async def archive_announcement(
    announcement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await CommunicationService(db).archive_announcement(
        _sid(user), user.id, announcement_id
    )


@router.delete("/announcements/{announcement_id}", status_code=204)
async def delete_announcement(
    announcement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    await CommunicationService(db).delete_announcement(_sid(user), announcement_id)


# ── conversations / messages ─────────────────────────────────


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).list_conversations(_sid(user), user.id)


@router.get("/contacts", response_model=list[MessageContactOut])
async def list_message_contacts(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).list_message_contacts(
        _sid(user), user.id, user.role.value
    )


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def start_conversation(
    body: ConversationCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).start_conversation(
        _sid(user), user.id, user.role.value, body
    )


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).list_messages(_sid(user), user.id, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageOut,
    status_code=201,
)
async def send_message(
    conversation_id: UUID,
    body: MessageCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[
        CurrentUser,
        Depends(
            require_roles(*CAMPUS_ROLES)
        ),
    ],
):
    return await CommunicationService(db).send_message(
        _sid(user), user.id, conversation_id, body
    )


@router.get("/stats", response_model=CommunicationStatsOut)
async def communication_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[CurrentUser, Depends(require_roles(UserRole.SCHOOL_ADMIN))],
):
    return await CommunicationService(db).stats(_sid(user))
