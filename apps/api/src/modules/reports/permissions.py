"""Report / analytics role gates (used by routers + services)."""

from uuid import UUID

from src.core.exceptions import ForbiddenError
from src.shared.enums import UserRole


def require_school_context(school_id: UUID | None) -> UUID:
    if school_id is None:
        raise ForbiddenError("School context required")
    return school_id


def can_view_school_analytics(role: str) -> bool:
    return role == UserRole.SCHOOL_ADMIN.value


def can_view_teacher_analytics(role: str) -> bool:
    return role in (UserRole.SCHOOL_ADMIN.value, UserRole.TEACHER.value)


def can_manage_reports(role: str) -> bool:
    return role == UserRole.SCHOOL_ADMIN.value


def can_export_reports(role: str) -> bool:
    return role == UserRole.SCHOOL_ADMIN.value
