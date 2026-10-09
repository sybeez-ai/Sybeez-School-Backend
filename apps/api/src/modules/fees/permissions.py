from src.core.exceptions import ForbiddenError
from src.shared.enums import UserRole


def assert_fees_admin(role: str) -> None:
    if role != UserRole.SCHOOL_ADMIN.value:
        raise ForbiddenError("School admin access required for fee management")


def assert_not_teacher_finance(role: str) -> None:
    if role == UserRole.TEACHER.value:
        raise ForbiddenError("Teachers cannot access financial records")
