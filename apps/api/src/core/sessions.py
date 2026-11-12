"""Per-role refresh lifetime.

School admin, finance, HR, payroll approver, platform staff, and the super admin
have an absolute 24h cap: rotating the refresh token does not extend it.
Teacher, student, and parent sessions rotate with a sliding lifetime (30 days
by default). Hours are configurable with SESSION_TTL_HOURS.
"""

from datetime import timedelta

from src.core.config import get_settings
from src.shared.enums import UserRole

DAILY_ROLES = frozenset(
    {
        UserRole.PLATFORM_ADMIN,
        UserRole.PLATFORM_STAFF,
        UserRole.SCHOOL_ADMIN,
        UserRole.FINANCE,
        UserRole.HR,
        UserRole.HR_MANAGER,
        UserRole.ACCOUNTANT,
        UserRole.CASHIER,
        UserRole.AUDITOR,
        UserRole.PAYROLL_ADMIN,
        UserRole.PAYROLL_APPROVER,
    }
)


def session_lifetime(role: UserRole | str) -> tuple[timedelta, bool]:
    """Return (lifetime, absolute). Absolute sessions cannot be extended by refresh."""
    settings = get_settings()
    key = role.value if isinstance(role, UserRole) else str(role)
    hours = settings.session_ttl_by_role.get(key)
    if hours is None:
        try:
            known = UserRole(key)
        except ValueError:
            known = None
        if known in DAILY_ROLES:
            hours = 24
        else:
            hours = 24 * 30
    absolute = False
    try:
        absolute = UserRole(key) in DAILY_ROLES
    except ValueError:
        absolute = True
    return timedelta(hours=hours), absolute
