"""Pure-function rules for HR leave accrual and letter placeholders."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from src.db.models import HrLeaveType
from src.modules.employees.lookup import EmployeeBrief
from src.modules.hr_leave_policy.service import entitled, fmt_days
from src.modules.hr_letters.service import (
    _UNFILLED_RE,
    BUILT_IN_TEMPLATES,
    fill,
    placeholder_values,
    render_template,
)


def _lt(quota, accrual="ANNUAL") -> HrLeaveType:
    return HrLeaveType(name="Casual", annual_quota=quota, accrual=accrual)


def test_entitled_without_quota_is_unlimited():
    assert entitled(_lt(None), 2026, date(2026, 10, 11)) is None


def test_entitled_annual_is_full_quota_up_front():
    assert entitled(_lt(Decimal(12)), 2026, date(2026, 1, 1)) == Decimal(12)


@pytest.mark.parametrize(
    ("year", "today", "expected"),
    [
        (2026, date(2026, 1, 15), Decimal(1)),
        (2026, date(2026, 10, 11), Decimal(10)),
        (2025, date(2026, 10, 11), Decimal(12)),
        (2027, date(2026, 10, 11), Decimal(0)),
    ],
)
def test_entitled_monthly_accrues_per_month(year, today, expected):
    assert entitled(_lt(Decimal(12), "MONTHLY"), year, today) == expected


def test_entitled_monthly_rounds_down_to_half_days():
    # 10 / 12 * 7 = 5.83 -> 5.5
    assert entitled(_lt(Decimal(10), "MONTHLY"), 2026, date(2026, 7, 1)) == Decimal("5.5")


def test_fmt_days_never_uses_exponent_notation():
    assert fmt_days(Decimal("10.0")) == "10"
    assert fmt_days(Decimal("1.50")) == "1.5"


def _emp(**kw) -> EmployeeBrief:
    base = {
        "id": uuid4(),
        "full_name": "Asha Rao",
        "employee_code": "EMP0007",
        "department_name": "Science",
        "designation_name": "Lab Assistant",
        "staff_type": "NON_TEACHING",
        "user_id": None,
        "email": None,
        "joining_date": date(2024, 6, 3),
        "title": None,
        "is_active": True,
    }
    base.update(kw)
    return EmployeeBrief(**base)


def test_placeholder_values_fall_back_when_fields_missing():
    v = placeholder_values(_emp(employee_code=None, department_name=None, designation_name=None,
                                joining_date=None), "Green Valley", "Principal")
    assert v["employee_ref"] == "Asha Rao"
    assert v["department"] == ""
    assert v["designation"] == "staff member"
    assert v["joining_date"] == "the agreed date"
    assert v["exit_date"] == "[last working day]"


def test_fill_replaces_known_tokens_only():
    out = fill("Hi {employee_name}, {unknown} {school_name}", {"employee_name": "A", "school_name": "S"} | {
        k: "" for k in placeholder_values(_emp(), "S", "P") if k not in ("employee_name", "school_name")
    })
    assert out == "Hi A, {unknown} S"


def test_every_built_in_template_renders_without_raw_tokens():
    for letter_type in BUILT_IN_TEMPLATES:
        tpl = render_template(letter_type, _emp(), "Green Valley", "Principal")
        assert "{" not in tpl.body and "{" not in tpl.title, letter_type
        assert "Green Valley" in tpl.body
        assert tpl.template_version is None


def test_unfilled_blanks_are_detected():
    exp = render_template("EXPERIENCE", _emp(), "GV", "P")
    assert _UNFILLED_RE.search(exp.body)
    filled = render_template("EXPERIENCE", _emp(exit_date=date(2026, 9, 30)), "GV", "P")
    assert "[last working day]" not in filled.body
