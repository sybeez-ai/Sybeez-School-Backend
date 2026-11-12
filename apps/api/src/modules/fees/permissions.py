from src.core.exceptions import ForbiddenError
from src.shared.enums import UserRole

FINANCE_VIEW = (
    UserRole.SCHOOL_ADMIN,
    UserRole.ACCOUNTANT,
    UserRole.CASHIER,
    UserRole.AUDITOR,
)
FINANCE_SETUP = (UserRole.SCHOOL_ADMIN, UserRole.ACCOUNTANT)
FINANCE_COLLECT = (UserRole.SCHOOL_ADMIN, UserRole.ACCOUNTANT, UserRole.CASHIER)
FINANCE_APPROVE = (UserRole.SCHOOL_ADMIN, UserRole.ACCOUNTANT)

FINANCE_VIEW_VALUES = {r.value for r in FINANCE_VIEW}
FINANCE_COLLECT_VALUES = {r.value for r in FINANCE_COLLECT}


def assert_fees_admin(role: str) -> None:
    if role not in {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value}:
        raise ForbiddenError("Accountant or school admin access required for fee setup")


def assert_not_teacher_finance(role: str) -> None:
    if role == UserRole.TEACHER.value:
        raise ForbiddenError("Teachers cannot access financial records")
