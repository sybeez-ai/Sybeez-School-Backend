"""Statutory math regressions: 87A marginal relief, surcharge, 80C cap scope, ESI rounding, LOP-adjusted PF/ESI."""

from datetime import date
from decimal import Decimal

from src.modules.payroll.calc import (
    NEW_REGIME_SLABS,
    OLD_REGIME_SLABS,
    ComponentDef,
    DayMark,
    EsiInput,
    PayContext,
    PfInput,
    TdsInput,
    WeeklyOff,
    compute_pay,
    esi,
    income_tax,
    tds,
    working_days,
)

D = Decimal


def test_new_regime_marginal_relief_just_above_rebate_limit():
    # 107500 x 12 = 12.9L gross, 12.15L taxable. Slab tax 62,250 but relief caps it at 15,000.
    result = tds(TdsInput(regime="NEW", monthly_taxable_earnings=D("107500"), rounding="PAISE"))
    assert result.taxable_income == D("1215000.00")
    assert result.tax_before_cess == D("15000.00")
    assert result.annual_tax == D("15600.00")
    assert result.monthly_tds == D("1300.00")


def test_marginal_relief_ends_where_slab_tax_is_lower():
    assert income_tax(D("1300000"), NEW_REGIME_SLABS, regime="NEW") == D("75000")


def test_old_regime_has_no_marginal_relief_for_rebate():
    assert income_tax(D("510000"), OLD_REGIME_SLABS, regime="OLD", rebate_limit=D("500000")) == D("14500.00")


def test_surcharge_and_surcharge_marginal_relief():
    assert income_tax(D("6000000"), NEW_REGIME_SLABS, regime="NEW") == D("1518000.00")
    # Just above 50L the surcharge is capped at the income above 50L.
    assert income_tax(D("5010000"), NEW_REGIME_SLABS, regime="NEW") == D("1090000")
    assert income_tax(D("5000000"), NEW_REGIME_SLABS, regime="NEW") == D("1080000.00")


def test_80d_and_other_chapter_via_sit_outside_80c_cap():
    result = tds(
        TdsInput(
            regime="OLD",
            monthly_taxable_earnings=D("100000"),
            declared_80c=D("200000"),
            declared_80d=D("25000"),
            other_chapter_vi=D("10000"),
            rounding="PAISE",
        )
    )
    assert result.chapter_vi_a == D("185000.00")


def test_one_time_arrears_tax_collected_in_the_month_paid():
    plain = tds(TdsInput(regime="NEW", monthly_taxable_earnings=D("150000"), rounding="PAISE"))
    assert plain.monthly_tds == D("12566.67")
    with_arrears = tds(
        TdsInput(regime="NEW", monthly_taxable_earnings=D("150000"), one_time_taxable=D("120000"), rounding="PAISE")
    )
    assert with_arrears.monthly_tds == D("37526.67")


def test_esi_rounds_up_to_next_rupee():
    result = esi(EsiInput(enrolled=True, gross=D("15001")))
    assert result.employee == D("113")
    assert result.employer == D("488")


def _marks(absent_days=()):
    days = working_days(date(2026, 10, 1), date(2026, 10, 31), [WeeklyOff(6, "ALL")], [])
    return [DayMark(d, "ABSENT" if d.day in absent_days else "PRESENT") for d in days]


def _ctx(marks, **kw):
    return PayContext(
        components=[
            ComponentDef("BASIC", "Basic", "EARNING", "FIXED", amount=D("12000"), pf_wage=True, display_order=1),
            ComponentDef("HRA", "HRA", "EARNING", "FIXED", amount=D("6000"), prorate=False, display_order=2),
        ],
        period_start=date(2026, 10, 1),
        period_end=date(2026, 10, 31),
        weekly=[WeeklyOff(6, "ALL")],
        holidays=[],
        marks=marks,
        pf=PfInput(enrolled=True, basic=D("12000")),
        esi_enrolled=True,
        **kw,
    )


def test_pf_and_esi_use_earned_wages_after_lop():
    pay = compute_pay(_ctx(_marks((5, 6))))
    lop = next(line.amount for line in pay.lines if line.code == "LOP")
    pf = next(line.amount for line in pay.lines if line.code == "PF")
    assert pf == ((D("12000") - lop) * D("0.12")).quantize(D("1"))
    full = compute_pay(_ctx(_marks()))
    assert next(line.amount for line in full.lines if line.code == "PF") == D("1440")
    assert pay.esi.employee < full.esi.employee


def test_esi_ignores_reimbursement_and_bonus():
    base = compute_pay(_ctx(_marks()))
    extra = compute_pay(_ctx(_marks(), reimbursements=D("5000"), bonus=D("4000")))
    assert base.esi.covered and extra.esi.covered
    assert extra.esi.employee == base.esi.employee


def test_mid_year_tds_projects_remaining_months_only():
    from src.modules.payroll.calc import fy_months_remaining

    assert fy_months_remaining(date(2026, 10, 31)) == 6
    # 6 months already paid at 100k with 30k TDS; 6 months left at 100k.
    result = tds(
        TdsInput(
            regime="NEW",
            monthly_taxable_earnings=D("100000"),
            prior_gross=D("600000"),
            prior_tds=D("30000"),
            months_remaining=6,
            rounding="PAISE",
        )
    )
    assert result.annual_gross == D("1200000.00")
    # Taxable 11.25L is under the rebate limit: nothing more to deduct.
    assert result.monthly_tds == D("0.00")
    joiner = tds(TdsInput(regime="NEW", monthly_taxable_earnings=D("150000"), months_remaining=6, rounding="PAISE"))
    assert joiner.annual_gross == D("900000.00")


def test_compute_pay_uses_remaining_months_for_tds():
    ctx = _ctx(_marks())
    pay = compute_pay(ctx)
    assert pay.tds.annual_gross == pay.gross * 6


def test_esi_continues_through_contribution_period():
    from src.modules.payroll.calc import esi_contribution_period

    assert esi_contribution_period(date(2026, 10, 31)) == (date(2026, 10, 1), date(2027, 3, 31))
    assert esi_contribution_period(date(2027, 2, 28)) == (date(2026, 10, 1), date(2027, 3, 31))
    assert esi_contribution_period(date(2026, 4, 30)) == (date(2026, 4, 1), date(2026, 9, 30))
    raised = [
        ComponentDef("BASIC", "Basic", "EARNING", "FIXED", amount=D("15000"), pf_wage=True, display_order=1),
        ComponentDef("HRA", "HRA", "EARNING", "FIXED", amount=D("8000"), prorate=False, display_order=2),
    ]
    dropped = compute_pay(PayContext(**{**_ctx(_marks()).__dict__, "components": raised}))
    assert dropped.esi.covered is False
    kept = compute_pay(PayContext(**{**_ctx(_marks()).__dict__, "components": raised, "esi_continuing": True}))
    assert kept.esi.covered is True
    assert kept.esi.employee == D("173")


def test_partial_period_factor_and_label():
    from src.modules.payroll.calc import partial_period_factor, period_label

    sunday = [WeeklyOff(6, "ALL")]
    assert partial_period_factor(date(2026, 10, 1), date(2026, 10, 31), sunday, []) == 1
    # Oct 2026 has 27 working days with Sundays off; Oct 1-11 has 9.
    assert partial_period_factor(date(2026, 10, 1), date(2026, 10, 11), sunday, []) == D(9) / D(27)
    assert period_label(date(2026, 10, 1), date(2026, 10, 31)) == "Payroll Oct 2026"
    assert period_label(date(2026, 10, 1), date(2026, 10, 11)) == "Payroll 1-11 Oct 2026 (part month)"
    base = _ctx(_marks())
    part = compute_pay(PayContext(**{**base.__dict__, "period_factor": D(9) / D(27)}))
    basic = next(line.amount for line in part.lines if line.code == "BASIC")
    assert basic == D("4000")
    hra = next(line.amount for line in part.lines if line.code == "HRA")
    assert hra == D("6000")  # not prorated in this structure
