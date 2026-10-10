"""Finance portal permissions. Auditor is read-only. Cashier collects, accountant approves."""

from __future__ import annotations

from src.core.exceptions import ForbiddenError
from src.shared.enums import UserRole

ACTIONS: dict[str, set[str]] = {
    "branches.write": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "fy.write": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "profile.write": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "concession_rule.write": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "concession.request": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "approve": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "collect": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value},
    "cheque.deposit": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value},
    "cheque.clear": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "cheque.bounce": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "receipt.cancel.request": {
        UserRole.SCHOOL_ADMIN.value,
        UserRole.ACCOUNTANT.value,
        UserRole.CASHIER.value,
    },
    "day_close.submit": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value},
    "day_close.verify": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "reports.read": {
        UserRole.SCHOOL_ADMIN.value,
        UserRole.ACCOUNTANT.value,
        UserRole.AUDITOR.value,
        UserRole.CASHIER.value,
    },
    "ledger.read": {
        UserRole.SCHOOL_ADMIN.value,
        UserRole.ACCOUNTANT.value,
        UserRole.AUDITOR.value,
        UserRole.CASHIER.value,
    },
    "dashboard.read": {
        UserRole.SCHOOL_ADMIN.value,
        UserRole.ACCOUNTANT.value,
        UserRole.AUDITOR.value,
        UserRole.CASHIER.value,
    },
    "audit.read": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.AUDITOR.value},
    "reminders.run": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "refund.request": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value},
    "staff_scope.write": {UserRole.SCHOOL_ADMIN.value},
}


def permissions_for(role: str) -> list[str]:
    return sorted(name for name, roles in ACTIONS.items() if role in roles)


def assert_finance(role: str, action: str) -> None:
    allowed = ACTIONS.get(action)
    if not allowed or role not in allowed:
        raise ForbiddenError(f"Role {role} cannot {action}")
