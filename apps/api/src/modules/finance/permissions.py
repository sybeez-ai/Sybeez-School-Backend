"""Finance portal permissions. Auditor is read-only. Cashier collects, accountant approves.

Per-person grants add an action the role does not have. A revoke removes an
action the role would otherwise have. Role defaults stay the same when no
override is loaded.
"""

from __future__ import annotations

from contextvars import ContextVar

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
    "route.manage": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
    "expense.write": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value, UserRole.CASHIER.value},
    "expense.approve": {UserRole.SCHOOL_ADMIN.value, UserRole.ACCOUNTANT.value},
}


def permissions_for(role: str) -> list[str]:
    return sorted(name for name, roles in ACTIONS.items() if role in roles)


_READ_ACTIONS = {"reports.read", "ledger.read", "dashboard.read", "audit.read"}
_loaded_permissions: ContextVar[tuple[str, set[str]] | None] = ContextVar(
    "finance_loaded_permissions", default=None
)


def bind_finance_permissions(role: str, permissions: set[str] | None) -> None:
    if permissions is None:
        _loaded_permissions.set(None)
        return
    _loaded_permissions.set((role, set(permissions)))


def _action_code(action: str) -> str:
    return "finance.read" if action in _READ_ACTIONS else "finance.manage"


def assert_finance(role: str, action: str) -> None:
    allowed = ACTIONS.get(action)
    role_ok = bool(allowed and role in allowed)
    loaded = _loaded_permissions.get()
    if loaded is None or loaded[0] != role:
        if not role_ok:
            raise ForbiddenError(f"Role {role} cannot {action}")
        return
    from src.modules.access.catalog import permissions_for as defaults_for

    perms = loaded[1]
    try:
        defaults = set(defaults_for(role))
    except Exception:
        defaults = set()
    code = _action_code(action)
    if role_ok:
        if code in defaults and code not in perms and not (
            code == "finance.read" and "finance.manage" in perms
        ):
            raise ForbiddenError(f"Missing permission: {code}")
        return
    extra = perms - defaults
    if code in extra or (code == "finance.read" and "finance.manage" in extra):
        return
    raise ForbiddenError(f"Role {role} cannot {action}")
