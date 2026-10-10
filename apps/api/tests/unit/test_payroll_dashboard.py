"""Dashboard helpers and the role set the frontend is allowed to call."""

from datetime import date

from src.modules.payroll.dashboard import month_bounds, money, statutory_due_on, trailing_months
from src.modules.payroll.permissions import DASHBOARD
from src.shared.enums import UserRole


def test_month_bounds_and_trend_length():
    start, end = month_bounds(date(2026, 2, 10))
    assert start == date(2026, 2, 1)
    assert end == date(2026, 2, 28)
    months = trailing_months(date(2026, 10, 31), 12)
    assert len(months) == 12
    assert months[0] == (date(2025, 11, 1), date(2025, 11, 30))
    assert months[-1] == (date(2026, 10, 1), date(2026, 10, 31))


def test_statutory_due_dates_roll_into_january():
    assert statutory_due_on(date(2026, 12, 31), 15) == date(2027, 1, 15)
    assert statutory_due_on(date(2026, 10, 31), 15) == date(2026, 11, 15)
    assert statutory_due_on(date(2026, 10, 31), 7) == date(2026, 11, 7)
    assert statutory_due_on(date(2026, 10, 31), 20) == date(2026, 11, 20)


def test_money_quantizes_half_up_display():
    assert money("1800.5") == "1800.50"
    assert money(None) == "0.00"


def test_dashboard_roles_exclude_approver_only():
    assert UserRole.PAYROLL_APPROVER.value not in DASHBOARD
    for role in (
        UserRole.SCHOOL_ADMIN,
        UserRole.PAYROLL_ADMIN,
        UserRole.HR_MANAGER,
        UserRole.ACCOUNTANT,
        UserRole.AUDITOR,
    ):
        assert role.value in DASHBOARD
