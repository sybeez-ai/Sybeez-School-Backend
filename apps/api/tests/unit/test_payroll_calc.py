"""Payroll calculation edge cases: PF, ESI, PT, TDS, LOP, rounding, approval."""

from datetime import date
from decimal import Decimal

import pytest

from src.modules.payroll.calc import (
    SEEDED_PT_SLABS,
    ComponentDef,
    DayMark,
    EsiInput,
    FormulaError,
    Holiday,
    PayContext,
    PfInput,
    TdsInput,
    WeeklyOff,
    approval_transition,
    completed_years,
    compute_pay,
    esi,
    eval_formula,
    gratuity,
    hra_exemption,
    loan_split,
    professional_tax,
    provident_fund,
    round_money,
    statutory_bonus,
    tds,
    within_geofence,
    working_days,
)

D = Decimal
SUNDAY = [WeeklyOff(6, "ALL")]
SCHOOL_WEEK = [WeeklyOff(6, "ALL"), WeeklyOff(5, "2"), WeeklyOff(5, "4")]


def test_rounding_half_up():
    assert round_money(D("2.50"), "NEAREST_RUPEE") == D("3")
    assert round_money(D("2.49"), "NEAREST_RUPEE") == D("2")
    assert round_money(D("10.005"), "PAISE") == D("10.01")


def test_pf_capped_and_full_and_opt_out():
    capped = provident_fund(PfInput(enrolled=True, basic=D("40000")))
    assert capped.employee == D("1800")
    assert capped.wage == D("15000.00")
    assert capped.eps == D("1250")
    assert capped.employer == D("1800")
    assert capped.employer_epf == D("550")

    full = provident_fund(PfInput(enrolled=True, basic=D("40000"), mode="FULL_BASIC"))
    assert full.employee == D("4800")
    assert full.eps == D("1250")
    assert full.employer_epf == D("3550")

    assert provident_fund(PfInput(enrolled=False, basic=D("40000"))).employee == 0
    before = provident_fund(
        PfInput(enrolled=True, basic=D("20000"), as_of=date(2026, 4, 1), effective_from=date(2026, 5, 1))
    )
    assert before.employee == 0
    vpf = provident_fund(PfInput(enrolled=True, basic=D("20000"), vpf_rate=D("5")))
    assert vpf.employee == D("1800")
    assert vpf.vpf == D("1000")
    da = provident_fund(PfInput(enrolled=True, basic=D("10000"), da=D("8000"), mode="CAPPED"))
    assert da.wage == D("15000.00")
    assert da.employee == D("1800")


def test_esi_threshold_edges():
    covered = esi(EsiInput(enrolled=True, gross=D("21000")))
    assert covered.covered is True
    assert covered.employee == D("158")
    assert covered.employer == D("683")
    assert esi(EsiInput(enrolled=True, gross=D("21000.01"))).covered is False
    assert esi(EsiInput(enrolled=False, gross=D("10000"))).employee == 0
    assert esi(
        EsiInput(enrolled=True, gross=D("10000"), as_of=date(2026, 8, 1), effective_to=date(2026, 7, 31))
    ).employee == 0


def test_professional_tax_seeded_states():
    assert professional_tax(SEEDED_PT_SLABS, state_code="KA", monthly_gross=D("24999.99"), month=6) == 0
    assert professional_tax(SEEDED_PT_SLABS, state_code="KA", monthly_gross=D("25000"), month=6) == D("200")
    assert professional_tax(SEEDED_PT_SLABS, state_code="KA", monthly_gross=D("25000"), month=2) == D("300")
    assert professional_tax(
        SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("25000"), month=6, gender="FEMALE"
    ) == 0
    assert professional_tax(
        SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("25000.01"), month=6, gender="FEMALE"
    ) == D("200")
    assert professional_tax(
        SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("10000"), month=6, gender="MALE"
    ) == D("175")
    assert professional_tax(
        SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("10000.01"), month=2, gender="MALE"
    ) == D("300")
    tn = professional_tax(SEEDED_PT_SLABS, state_code="TN", monthly_gross=D("20000"), month=8, rounding="PAISE")
    assert tn == D("208.33")
    assert professional_tax(SEEDED_PT_SLABS, state_code="DL", monthly_gross=D("80000"), month=1) == 0


def test_tds_new_regime_rebate_and_just_over():
    under = tds(TdsInput(regime="NEW", monthly_taxable_earnings=D("106250")))
    assert under.taxable_income == D("1200000.00")
    assert under.monthly_tds == 0
    assert under.rebate_applied is True
    over = tds(TdsInput(regime="NEW", monthly_taxable_earnings=D("106251"), rounding="PAISE"))
    assert over.rebate_applied is False
    assert over.monthly_tds > 0
    assert over.cess > 0


def test_tds_old_regime_pf_hra_and_rebate():
    exempt = hra_exemption(
        annual_hra=D("240000"), annual_basic=D("480000"), annual_rent=D("180000"), metro=True
    )
    assert exempt == D("132000.00")
    result = tds(
        TdsInput(
            regime="OLD",
            monthly_taxable_earnings=D("60000"),
            monthly_basic=D("40000"),
            monthly_hra=D("20000"),
            monthly_employee_pf=D("1800"),
            annual_rent=D("180000"),
            metro=True,
            declared_80c=D("100000"),
            rounding="PAISE",
        )
    )
    assert result.regime == "OLD"
    assert result.hra_exemption > 0
    assert result.chapter_vi_a == D("121600.00")
    zero = tds(TdsInput(regime="OLD", monthly_taxable_earnings=D("40000")))
    assert zero.taxable_income == D("430000.00")
    assert zero.monthly_tds == 0


def test_working_days_weekly_off_and_holiday_kinds():
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    days = working_days(start, end, SCHOOL_WEEK, [])
    assert len(days) == 25
    assert date(2026, 10, 4) not in days
    assert date(2026, 10, 10) not in days
    assert date(2026, 10, 24) not in days
    assert date(2026, 10, 3) in days
    holidays = [
        Holiday(date(2026, 10, 2), "NATIONAL"),
        Holiday(date(2026, 10, 5), "OPTIONAL"),
        Holiday(date(2026, 10, 6), "SCHOOL", "Primary"),
    ]
    trimmed = working_days(start, end, SCHOOL_WEEK, holidays, branch_name="Primary")
    assert date(2026, 10, 2) not in trimmed
    assert date(2026, 10, 5) in trimmed
    assert date(2026, 10, 6) not in trimmed
    other = working_days(start, end, SCHOOL_WEEK, holidays, branch_name="Senior")
    assert date(2026, 10, 6) in other


def test_lop_and_salary_with_half_day():
    start, end = date(2026, 10, 1), date(2026, 10, 31)
    days = working_days(start, end, SUNDAY, [])
    marks = [DayMark(day, "PRESENT") for day in days]
    marks[0] = DayMark(days[0], "ABSENT")
    marks[1] = DayMark(days[1], "HALF_DAY")
    marks[2] = DayMark(days[2], "LEAVE", paid_leave=True)
    ctx = PayContext(
        components=[
            ComponentDef("BASIC", "Basic", "EARNING", "FIXED", amount=D("30000"), pf_wage=True, display_order=1),
            ComponentDef(
                "HRA", "HRA", "EARNING", "PERCENT", percent=D("40"), percent_of="BASIC", prorate=False, display_order=2
            ),
            ComponentDef(
                "CANTEEN", "Canteen", "DEDUCTION", "FIXED", amount=D("500"), taxable=False, prorate=False, display_order=3
            ),
        ],
        period_start=start,
        period_end=end,
        weekly=SUNDAY,
        holidays=[],
        marks=marks,
        pf=PfInput(enrolled=True, basic=D("30000")),
        pt_slabs=SEEDED_PT_SLABS,
        pt_state="KA",
        regime="NEW",
        rounding="NEAREST_RUPEE",
    )
    pay = compute_pay(ctx)
    assert pay.lop_days == D("1.5")
    assert pay.paid_days == pay.working_days - D("1.5")
    lop = next(line.amount for line in pay.lines if line.code == "LOP")
    assert lop == round_money(D("30000") / pay.working_days * D("1.5"))
    assert any(line.code == "PF" and line.amount == D("1800") for line in pay.lines)
    assert any(line.code == "CANTEEN" for line in pay.lines)
    assert pay.net == pay.gross - pay.deductions
    assert pay.form16["regime"] == "NEW"


def test_formula_rejects_calls_and_unknown_names():
    variables = {"BASIC": D("10000")}
    assert eval_formula("BASIC * 0.1 + 50", variables) == D("1050.0")
    with pytest.raises(FormulaError):
        eval_formula("__import__('os')", variables)
    with pytest.raises(FormulaError):
        eval_formula("BASIC + BONUS", variables)


def test_unmarked_day_is_lop_and_zero_working_days_pay_full():
    start = end = date(2026, 10, 4)
    pay = compute_pay(
        PayContext(
            components=[ComponentDef("BASIC", "Basic", "EARNING", "FIXED", amount=D("1000"), display_order=1)],
            period_start=start,
            period_end=end,
            weekly=SUNDAY,
            holidays=[],
            marks=[],
            pt_slabs=[],
            pt_state="KA",
        )
    )
    assert pay.working_days == 0 or pay.lop_days == pay.working_days
    assert pay.gross == D("1000")


def test_bonus_gratuity_loan_and_geofence():
    assert statutory_bonus(enabled=False, month=3, bonus_month=3, basic=D("7000"), da=0) == 0
    assert statutory_bonus(enabled=True, month=3, bonus_month=3, basic=D("25000"), da=0) == 0
    assert statutory_bonus(enabled=True, month=3, bonus_month=3, basic=D("7000"), da=0) == D("583")
    assert gratuity(basic=D("26000"), da=0, years=D("4")) == 0
    assert gratuity(basic=D("26000"), da=0, years=D("5")) == D("75000")
    assert completed_years(date(2020, 10, 10), date(2026, 10, 10)) >= D("5")
    interest, principal, balance = loan_split(balance=D("10000"), emi=D("1000"), annual_rate=D("12"))
    assert interest == D("100")
    assert principal == D("900")
    assert balance == D("9100")
    assert within_geofence(
        enabled=True, lat=12.97, lng=77.59, centre_lat=12.97, centre_lng=77.59, radius_m=100
    ) == "INSIDE"
    assert within_geofence(
        enabled=True, lat=13.1, lng=77.59, centre_lat=12.97, centre_lng=77.59, radius_m=100
    ) == "OUTSIDE"
    assert within_geofence(
        enabled=False, lat=None, lng=None, centre_lat=12.97, centre_lng=77.59, radius_m=100
    ) == "DISABLED"
    assert within_geofence(
        enabled=True, lat=None, lng=None, centre_lat=12.97, centre_lng=77.59, radius_m=100
    ) == "NOT_CAPTURED"


def test_maker_checker_transitions():
    assert approval_transition(
        status="DRAFT", action="SUBMIT", actor_id="a", submitter_id=None, actor_can_approve=False
    ) == "PENDING_APPROVAL"
    with pytest.raises(ValueError, match="cannot approve"):
        approval_transition(
            status="PENDING_APPROVAL",
            action="APPROVE",
            actor_id="a",
            submitter_id="a",
            actor_can_approve=True,
        )
    assert approval_transition(
        status="PENDING_APPROVAL",
        action="APPROVE",
        actor_id="b",
        submitter_id="a",
        actor_can_approve=True,
    ) == "APPROVED"
    with pytest.raises(ValueError, match="Approve the run"):
        approval_transition(
            status="PENDING_APPROVAL",
            action="PUBLISH",
            actor_id="b",
            submitter_id="a",
            actor_can_approve=True,
        )
    with pytest.raises(ValueError):
        approval_transition(
            status="APPROVED", action="APPROVE", actor_id="b", submitter_id="a", actor_can_approve=False
        )
