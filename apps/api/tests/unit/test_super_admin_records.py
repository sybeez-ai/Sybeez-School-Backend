"""Super Admin commercial terms and delete guards. No database and no live deletes."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.api.dependencies import CurrentUser, is_super_admin
from src.core.exceptions import ValidationError
from src.modules.platform.commercial import billing_status, days_left, quote
from src.modules.platform.records import KINDS, RecordDeleteIn, delete_record, names_match
from src.shared.enums import UserRole


def test_quote_applies_percent_then_amount():
    got = quote(100, 10, 10, 50)
    assert got == {"gross_amount": 1000.0, "discount_value": 150.0, "total_amount": 850.0}


def test_quote_never_goes_below_zero():
    got = quote(10, 1, 0, 500)
    assert got["total_amount"] == 0.0


def test_billing_status_trial_active_and_expired():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    assert billing_status(subscription_status="TRIAL", trial_ends_at=now + timedelta(days=3), renewal_at=None, now=now) == "trial"
    assert billing_status(subscription_status="TRIAL", trial_ends_at=now - timedelta(days=1), renewal_at=None, now=now) == "expired"
    assert billing_status(subscription_status="ACTIVE", trial_ends_at=None, renewal_at=now + timedelta(days=10), now=now) == "active"
    assert billing_status(subscription_status="ACTIVE", trial_ends_at=None, renewal_at=now - timedelta(seconds=1), now=now) == "expired"
    assert billing_status(subscription_status="CANCELLED", trial_ends_at=None, renewal_at=None, now=now) == "expired"


def test_days_left_rounds_up_a_partial_day():
    now = datetime(2026, 6, 1, 12, tzinfo=UTC)
    assert days_left(now + timedelta(hours=3), now) == 1
    assert days_left(now - timedelta(days=1), now) == 0
    assert days_left(None, now) == 0


def test_school_trial_days_fall_back_to_the_platform_default():
    from src.modules.platform.commercial import effective_trial_days

    assert effective_trial_days(None, 30) == 30
    assert effective_trial_days(None, None) == 30
    assert effective_trial_days(14, 30) == 14


def test_ai_consumption_is_token_counts_only():
    from uuid import UUID

    from src.modules.platform.ai_consumption import school_token_rows

    school = UUID("00000000-0000-0000-0000-0000000000aa")
    rows = school_token_rows([(school, "North", 10), (school, "North", 5), (None, None, 2)])
    assert rows[0]["tokens"] == 15
    assert rows[0]["school_name"] == "North"
    assert "cost" not in rows[0] and "amount" not in rows[0]
    assert rows[1]["school_name"] == "Platform"
    assert rows[1]["tokens"] == 2


def test_school_name_confirmation_is_exact():
    assert names_match("St. Mary's", "St. Mary's")
    assert names_match("  North School  ", "North School")
    assert not names_match("North School", "north school")
    assert not names_match("North School", "North")


def test_record_catalog_covers_the_platform():
    keys = {item["key"] for item in KINDS}
    assert {
        "schools",
        "users",
        "platform_staff",
        "employees",
        "students",
        "parents",
        "teachers",
        "classes",
        "fee_invoices",
        "fee_payments",
        "payroll_runs",
        "payslips",
        "expenses",
        "documents",
        "family_documents",
        "fee_settings",
        "payroll_settings",
        "hr_settings",
    } <= keys
    assert next(item for item in KINDS if item["key"] == "schools")["confirm"] == "name"


def test_only_the_two_allowlisted_emails_are_super_admin():
    from src.api.dependencies import superadmin_allowlist

    assert superadmin_allowlist() == frozenset({"admin@sybeez.ai", "silverstalan.inbaraj@sybeez.ai"})
    silver = CurrentUser(
        id=uuid4(),
        email="Silverstalan.Inbaraj@sybeez.ai",
        full_name="Silver",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )
    labeled_only = CurrentUser(
        id=uuid4(),
        email="platform.dev@sybeez.ai",
        full_name="Dev",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )
    assert is_super_admin(silver)
    assert not is_super_admin(labeled_only)


def test_super_admin_flag_is_not_every_platform_admin():
    owner = CurrentUser(
        id=uuid4(),
        email="admin@sybeez.ai",
        full_name="Owner",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )
    operator = CurrentUser(
        id=uuid4(),
        email="ops@sybeez.ai",
        full_name="Ops",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="ADMIN",
    )
    assert is_super_admin(owner)
    assert not is_super_admin(operator)


@pytest.mark.asyncio
async def test_delete_refuses_without_confirmation():
    actor = CurrentUser(
        id=uuid4(),
        email="admin@sybeez.ai",
        full_name="Owner",
        role=UserRole.PLATFORM_ADMIN,
        school_id=None,
        membership_id=None,
        is_platform_admin=True,
        platform_role="SUPER_ADMIN",
    )
    with pytest.raises(ValidationError):
        await delete_record(None, actor, "students", uuid4(), RecordDeleteIn(confirm=False))
    with pytest.raises(ValidationError):
        await delete_record(None, actor, "schools", uuid4(), RecordDeleteIn(confirm_name="  "))
