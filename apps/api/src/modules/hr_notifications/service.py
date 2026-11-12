"""HR events delivered through the shared in-app notifications table (same inbox and bell as other modules)."""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import SchoolMembership, User
from src.modules.hr_settings.service import notifications_enabled
from src.modules.notifications.service import create_bulk_notifications
from src.shared.enums import MembershipStatus, NotificationType, UserRole

LEAVE_SUBMITTED = NotificationType.LEAVE_SUBMITTED.value
LEAVE_APPROVED = NotificationType.LEAVE_APPROVED.value
LEAVE_REJECTED = NotificationType.LEAVE_REJECTED.value
HR_REQUEST = "HR_REQUEST"
HR_LETTER = "HR_LETTER"
HR_ONBOARDING = "HR_ONBOARDING"
HR_DOCUMENT = "HR_DOCUMENT"

# Student/parent leave shares the LEAVE_* categories, so the HRM inbox is selected by entity type.
HR_ENTITY_TYPES = ("hr_leave", "hr_request", "hr_workflow", "hr_document", "hr_letter", "payroll_payslip")

ADMIN_LINKS = {
    "leave": "/hrm/leave",
    "request": "/hrm/self-service",
    "workflow": "/hrm/onboarding",
    "document": "/hrm/files",
    "letter": "/hrm/letters",
}
EMPLOYEE_LINKS = {
    "leave": "/teacher/leave",
    "request": "/teacher/my-hr",
    "workflow": "/teacher/my-hr",
    "document": "/teacher/my-hr",
    "letter": "/teacher/my-hr",
}
STAFF_LINKS = {
    "leave": "/staff/leave",
    "request": "/staff/my-hr",
    "workflow": "/staff/my-hr",
    "document": "/staff/my-hr",
    "letter": "/staff/my-hr",
}


def event_key(entity_type: str, entity_id: UUID, event: str) -> str:
    """Stable key for one business event; the (event_key, user_id) unique index makes replays no-ops."""
    return f"hr:{entity_type}:{entity_id}:{event}"[:240]


class HrNotifier:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def admin_ids(self, school_id: UUID) -> list[UUID]:
        rows = await self.db.execute(
            select(User.id)
            .join(SchoolMembership, SchoolMembership.user_id == User.id)
            .where(
                SchoolMembership.school_id == school_id,
                SchoolMembership.role.in_(
                    (UserRole.SCHOOL_ADMIN.value, UserRole.HR.value, UserRole.HR_MANAGER.value)
                ),
                SchoolMembership.status == MembershipStatus.ACTIVE.value,
                User.is_active.is_(True),
            )
            .distinct()
        )
        return list(rows.scalars())

    async def _enabled(self, school_id: UUID, category: str) -> bool:
        return await notifications_enabled(self.db, school_id, category)

    async def employee_links(self, school_id: UUID, user_id: UUID | None) -> dict[str, str]:
        """Self-service links for the portal this employee signs in to (teacher or staff)."""
        if user_id is None:
            return EMPLOYEE_LINKS
        roles = set(
            (
                await self.db.execute(
                    select(SchoolMembership.role).where(
                        SchoolMembership.school_id == school_id,
                        SchoolMembership.user_id == user_id,
                    )
                )
            ).scalars()
        )
        if UserRole.STAFF.value in roles and UserRole.TEACHER.value not in roles:
            return STAFF_LINKS
        return EMPLOYEE_LINKS

    async def send(
        self,
        school_id: UUID,
        recipients: Iterable[UUID | None],
        *,
        category: str,
        title: str,
        body: str,
        link: str,
        entity_type: str,
        entity_id: UUID,
        event: str,
        actor: UUID | None = None,
    ) -> int:
        """Notify `recipients` (minus the actor who caused the event). Returns rows created.

        `event` names the business event (e.g. ``submitted``); delivering the same event to the same
        user twice is a no-op, so retries and double submits never duplicate an inbox entry.
        """
        ids = [u for u in dict.fromkeys(recipients) if u and u != actor]
        if not ids or not await self._enabled(school_id, category):
            return 0
        return await create_bulk_notifications(
            self.db,
            school_id=school_id,
            recipient_user_ids=ids,
            title=title[:255],
            body=body,
            category=category,
            link_path=link,
            entity_type=entity_type,
            entity_id=entity_id,
            event_key_prefix=event_key(entity_type, entity_id, event),
        )

    async def to_admins(
        self,
        school_id: UUID,
        *,
        kind: str,
        category: str,
        title: str,
        body: str,
        entity_id: UUID,
        event: str,
        actor: UUID | None,
    ) -> int:
        return await self.send(
            school_id,
            await self.admin_ids(school_id),
            category=category,
            title=title,
            body=body,
            link=ADMIN_LINKS[kind],
            entity_type=f"hr_{kind}",
            entity_id=entity_id,
            event=event,
            actor=actor,
        )

    async def to_employee(
        self,
        school_id: UUID,
        user_id: UUID | None,
        *,
        kind: str,
        category: str,
        title: str,
        body: str,
        entity_id: UUID,
        event: str,
        actor: UUID | None,
    ) -> int:
        links = await self.employee_links(school_id, user_id)
        return await self.send(
            school_id,
            [user_id],
            category=category,
            title=title,
            body=body,
            link=links[kind],
            entity_type=f"hr_{kind}",
            entity_id=entity_id,
            event=event,
            actor=actor,
        )
