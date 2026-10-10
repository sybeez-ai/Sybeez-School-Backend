"""Indian school payroll calculations.

Pure functions. Amounts use Decimal and the school's rounding mode.
Defaults match the open questions in docs/PAYROLL.md: PF 12 percent of basic
capped at 15,000, ESI 0.75/3.25 with a 21,000 gross threshold, new tax regime,
and the Karnataka, Tamil Nadu, and Maharashtra professional-tax slabs seeded
for FY 2026-27 practice. Schools can replace every rate.
"""

from __future__ import annotations

import ast
import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import asin, cos, radians, sin, sqrt

PAISE = Decimal("0.01")
RUPEE = Decimal("1")
ZERO = Decimal("0.00")
HUNDRED = Decimal("100")

RESERVED_CODES = frozenset({"PF", "VPF", "ESI", "PT", "TDS", "LOP", "GRATUITY", "EPS"})


def D(value: Decimal | int | str | float) -> Decimal:
    return Decimal(str(value))


def round_money(value: Decimal, mode: str = "NEAREST_RUPEE") -> Decimal:
    quantum = PAISE if mode == "PAISE" else RUPEE
    return D(value).quantize(quantum, rounding=ROUND_HALF_UP)


def _rate(value: Decimal) -> Decimal:
    return D(value) / HUNDRED


@dataclass(frozen=True)
class PfInput:
    enrolled: bool
    basic: Decimal
    da: Decimal = ZERO
    employee_rate: Decimal = Decimal("12")
    employer_rate: Decimal = Decimal("12")
    eps_rate: Decimal = Decimal("8.33")
    ceiling: Decimal = Decimal("15000")
    mode: str = "CAPPED"  # CAPPED | FULL_BASIC
    as_of: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    vpf_rate: Decimal | None = None
    vpf_amount: Decimal | None = None
    rounding: str = "NEAREST_RUPEE"


@dataclass(frozen=True)
class PfResult:
    employee: Decimal
    employer: Decimal
    eps: Decimal
    employer_epf: Decimal
    vpf: Decimal
    wage: Decimal


def _in_force(as_of: date | None, start: date | None, end: date | None) -> bool:
    if as_of is None:
        return True
    if start is not None and as_of < start:
        return False
    if end is not None and as_of > end:
        return False
    return True


def provident_fund(spec: PfInput) -> PfResult:
    """Employee and employer PF. EPS stays inside the wage ceiling even when PF is on full basic."""
    if not spec.enrolled or not _in_force(spec.as_of, spec.effective_from, spec.effective_to):
        return PfResult(ZERO, ZERO, ZERO, ZERO, ZERO, ZERO)
    raw = D(spec.basic) + D(spec.da)
    if raw < 0:
        raw = ZERO
    wage = raw if spec.mode == "FULL_BASIC" else min(raw, D(spec.ceiling))
    eps_wage = min(raw, D(spec.ceiling))
    employee = round_money(wage * _rate(spec.employee_rate), spec.rounding)
    employer = round_money(wage * _rate(spec.employer_rate), spec.rounding)
    eps = round_money(eps_wage * _rate(spec.eps_rate), spec.rounding)
    if eps > employer:
        eps = employer
    employer_epf = round_money(employer - eps, spec.rounding)
    if spec.vpf_amount is not None:
        vpf = round_money(D(spec.vpf_amount), spec.rounding)
    elif spec.vpf_rate is not None:
        vpf = round_money(D(spec.basic) * _rate(spec.vpf_rate), spec.rounding)
    else:
        vpf = ZERO
    if vpf < 0:
        vpf = ZERO
    return PfResult(employee, employer, eps, employer_epf, vpf, round_money(wage, "PAISE"))


@dataclass(frozen=True)
class EsiInput:
    enrolled: bool
    gross: Decimal
    threshold: Decimal = Decimal("21000")
    employee_rate: Decimal = Decimal("0.75")
    employer_rate: Decimal = Decimal("3.25")
    as_of: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    rounding: str = "NEAREST_RUPEE"


@dataclass(frozen=True)
class EsiResult:
    employee: Decimal
    employer: Decimal
    covered: bool


def esi(spec: EsiInput) -> EsiResult:
    """Monthly ESI. Gross above the threshold is not covered for that month."""
    if not spec.enrolled or not _in_force(spec.as_of, spec.effective_from, spec.effective_to):
        return EsiResult(ZERO, ZERO, False)
    gross = D(spec.gross)
    if gross <= 0 or gross > D(spec.threshold):
        return EsiResult(ZERO, ZERO, False)
    return EsiResult(
        round_money(gross * _rate(spec.employee_rate), spec.rounding),
        round_money(gross * _rate(spec.employer_rate), spec.rounding),
        True,
    )


@dataclass(frozen=True)
class PtSlab:
    state_code: str
    gender: str  # ANY | MALE | FEMALE
    min_amount: Decimal
    max_amount: Decimal | None
    amount: Decimal
    february_amount: Decimal | None = None
    frequency: str = "MONTHLY"  # MONTHLY | HALF_YEARLY
    basis: str = "MONTHLY_GROSS"  # MONTHLY_GROSS | HALF_YEAR_GROSS


def professional_tax(
    slabs: list[PtSlab],
    *,
    state_code: str,
    monthly_gross: Decimal,
    month: int,
    gender: str = "OTHER",
    rounding: str = "NEAREST_RUPEE",
) -> Decimal:
    """Pick the slab and return this month's deduction.

    Half-yearly slabs (Tamil Nadu) are stored against half-year income and
    spread across the six months so a monthly run still collects them.
    February can carry the extra rupee some states use to reach the annual cap.
    """
    state = state_code.strip().upper()
    gender = (gender or "OTHER").upper()
    gross = D(monthly_gross)
    chosen = [s for s in slabs if s.state_code.upper() == state]
    if not chosen:
        return ZERO
    half = any(s.frequency == "HALF_YEARLY" or s.basis == "HALF_YEAR_GROSS" for s in chosen)
    probe = gross * 6 if half else gross

    def _matches(slab: PtSlab) -> bool:
        if slab.gender not in {"ANY", gender}:
            # Female-specific slabs win over ANY when both match; handled below.
            return False
        if probe < D(slab.min_amount):
            return False
        if slab.max_amount is not None and probe > D(slab.max_amount):
            return False
        return True

    specific = [s for s in chosen if s.gender == gender and _matches(s)]
    general = [s for s in chosen if s.gender == "ANY" and _matches(s)]
    hit = (specific or general)
    if not hit:
        return ZERO
    slab = hit[0]
    amount = D(slab.amount)
    if not half and month == 2 and slab.february_amount is not None:
        amount = D(slab.february_amount)
    if half:
        amount = amount / Decimal("6")
    return round_money(amount, rounding)


@dataclass(frozen=True)
class TaxSlab:
    upto: Decimal | None
    rate: Decimal  # percent


NEW_REGIME_SLABS = (
    TaxSlab(Decimal("400000"), Decimal("0")),
    TaxSlab(Decimal("800000"), Decimal("5")),
    TaxSlab(Decimal("1200000"), Decimal("10")),
    TaxSlab(Decimal("1600000"), Decimal("15")),
    TaxSlab(Decimal("2000000"), Decimal("20")),
    TaxSlab(Decimal("2400000"), Decimal("25")),
    TaxSlab(None, Decimal("30")),
)
OLD_REGIME_SLABS = (
    TaxSlab(Decimal("250000"), Decimal("0")),
    TaxSlab(Decimal("500000"), Decimal("5")),
    TaxSlab(Decimal("1000000"), Decimal("20")),
    TaxSlab(None, Decimal("30")),
)


def slab_tax(taxable: Decimal, slabs: tuple[TaxSlab, ...] | list[TaxSlab]) -> Decimal:
    income = D(taxable)
    if income <= 0:
        return ZERO
    tax = ZERO
    previous = ZERO
    for slab in slabs:
        cap = D(slab.upto) if slab.upto is not None else income
        portion = min(income, cap) - previous
        if portion > 0:
            tax += portion * _rate(slab.rate)
        if slab.upto is None or income <= D(slab.upto):
            break
        previous = D(slab.upto)
    return tax


@dataclass(frozen=True)
class TdsInput:
    regime: str = "NEW"  # NEW | OLD
    monthly_taxable_earnings: Decimal = ZERO
    monthly_basic: Decimal = ZERO
    monthly_hra: Decimal = ZERO
    monthly_employee_pf: Decimal = ZERO
    monthly_pt: Decimal = ZERO
    annual_rent: Decimal = ZERO
    metro: bool = False
    declared_80c: Decimal = ZERO
    declared_80d: Decimal = ZERO
    other_chapter_vi: Decimal = ZERO
    one_time_taxable: Decimal = ZERO
    standard_deduction_new: Decimal = Decimal("75000")
    standard_deduction_old: Decimal = Decimal("50000")
    rebate_limit_new: Decimal = Decimal("1200000")
    rebate_limit_old: Decimal = Decimal("500000")
    chapter_vi_cap: Decimal = Decimal("150000")
    cess_rate: Decimal = Decimal("4")
    new_slabs: tuple[TaxSlab, ...] = NEW_REGIME_SLABS
    old_slabs: tuple[TaxSlab, ...] = OLD_REGIME_SLABS
    rounding: str = "NEAREST_RUPEE"


@dataclass(frozen=True)
class TdsResult:
    regime: str
    annual_gross: Decimal
    standard_deduction: Decimal
    hra_exemption: Decimal
    chapter_vi_a: Decimal
    professional_tax_annual: Decimal
    taxable_income: Decimal
    tax_before_cess: Decimal
    cess: Decimal
    annual_tax: Decimal
    monthly_tds: Decimal
    rebate_applied: bool


def hra_exemption(*, annual_hra: Decimal, annual_basic: Decimal, annual_rent: Decimal, metro: bool) -> Decimal:
    if annual_hra <= 0 or annual_rent <= 0 or annual_basic <= 0:
        return ZERO
    percent = Decimal("0.50") if metro else Decimal("0.40")
    rent_excess = annual_rent - (annual_basic * Decimal("0.10"))
    if rent_excess < 0:
        rent_excess = ZERO
    return min(annual_hra, annual_basic * percent, rent_excess)


def tds(spec: TdsInput) -> TdsResult:
    """Project this month across the year. One-time arrears are taxed once, not times twelve."""
    regime = "OLD" if spec.regime.upper() == "OLD" else "NEW"
    annual_gross = D(spec.monthly_taxable_earnings) * 12 + D(spec.one_time_taxable)
    std = D(spec.standard_deduction_old if regime == "OLD" else spec.standard_deduction_new)
    annual_basic = D(spec.monthly_basic) * 12
    annual_hra = D(spec.monthly_hra) * 12
    exempt_hra = ZERO
    chapter = ZERO
    pt_annual = ZERO
    if regime == "OLD":
        exempt_hra = hra_exemption(
            annual_hra=annual_hra,
            annual_basic=annual_basic,
            annual_rent=D(spec.annual_rent),
            metro=spec.metro,
        )
        pf_annual = D(spec.monthly_employee_pf) * 12
        chapter_raw = pf_annual + D(spec.declared_80c) + D(spec.declared_80d) + D(spec.other_chapter_vi)
        chapter = min(chapter_raw, D(spec.chapter_vi_cap))
        pt_annual = D(spec.monthly_pt) * 12
    taxable = annual_gross - std - exempt_hra - chapter - pt_annual
    if taxable < 0:
        taxable = ZERO
    slabs = spec.old_slabs if regime == "OLD" else spec.new_slabs
    tax = slab_tax(taxable, slabs)
    limit = D(spec.rebate_limit_old if regime == "OLD" else spec.rebate_limit_new)
    rebate = taxable <= limit and tax > 0
    if taxable <= limit:
        tax = ZERO
    cess = tax * _rate(spec.cess_rate)
    annual = tax + cess
    return TdsResult(
        regime=regime,
        annual_gross=round_money(annual_gross, "PAISE"),
        standard_deduction=round_money(std, "PAISE"),
        hra_exemption=round_money(exempt_hra, "PAISE"),
        chapter_vi_a=round_money(chapter, "PAISE"),
        professional_tax_annual=round_money(pt_annual, "PAISE"),
        taxable_income=round_money(taxable, "PAISE"),
        tax_before_cess=round_money(tax, spec.rounding),
        cess=round_money(cess, spec.rounding),
        annual_tax=round_money(annual, spec.rounding),
        monthly_tds=round_money(annual / 12, spec.rounding),
        rebate_applied=rebate or (taxable <= limit),
    )


def form16_lines(result: TdsResult, *, employee_name: str, pan: str | None, financial_year: str) -> dict:
    return {
        "financial_year": financial_year,
        "employee_name": employee_name,
        "pan": pan,
        "regime": result.regime,
        "gross_salary": str(result.annual_gross),
        "exempt_hra": str(result.hra_exemption),
        "standard_deduction": str(result.standard_deduction),
        "professional_tax": str(result.professional_tax_annual),
        "chapter_vi_a": str(result.chapter_vi_a),
        "taxable_salary": str(result.taxable_income),
        "tax": str(result.tax_before_cess),
        "cess": str(result.cess),
        "total_tax": str(result.annual_tax),
        "monthly_tds": str(result.monthly_tds),
    }


@dataclass(frozen=True)
class WeeklyOff:
    weekday: int  # Monday 0 .. Sunday 6
    occurrence: str = "ALL"  # ALL | 1 | 2 | 3 | 4 | 5 | LAST


def nth_weekday(day: date) -> int:
    return (day.day - 1) // 7 + 1


def is_last_weekday(day: date) -> bool:
    return (day + timedelta(days=7)).month != day.month


def is_weekly_off(day: date, rules: list[WeeklyOff]) -> bool:
    for rule in rules:
        if day.weekday() != rule.weekday:
            continue
        occ = rule.occurrence.upper()
        if occ == "ALL":
            return True
        if occ == "LAST" and is_last_weekday(day):
            return True
        if occ.isdigit() and nth_weekday(day) == int(occ):
            return True
    return False


@dataclass(frozen=True)
class Holiday:
    day: date
    kind: str  # NATIONAL | STATE | SCHOOL | OPTIONAL | RESTRICTED
    branch_name: str | None = None


_FULL_HOLIDAYS = frozenset({"NATIONAL", "STATE", "SCHOOL"})


def is_full_holiday(day: date, holidays: list[Holiday], branch_name: str | None = None) -> bool:
    for holiday in holidays:
        if holiday.day != day or holiday.kind.upper() not in _FULL_HOLIDAYS:
            continue
        if holiday.branch_name and branch_name and holiday.branch_name.lower() != branch_name.lower():
            continue
        if holiday.branch_name and not branch_name:
            continue
        return True
    return False


def iter_dates(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def working_days(
    start: date,
    end: date,
    weekly: list[WeeklyOff],
    holidays: list[Holiday],
    branch_name: str | None = None,
) -> list[date]:
    if end < start:
        return []
    return [
        day
        for day in iter_dates(start, end)
        if not is_weekly_off(day, weekly) and not is_full_holiday(day, holidays, branch_name)
    ]


def last_working_day(
    year: int,
    month: int,
    weekly: list[WeeklyOff],
    holidays: list[Holiday],
    branch_name: str | None = None,
) -> date:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    days = working_days(start, end, weekly, holidays, branch_name)
    return days[-1] if days else end


@dataclass(frozen=True)
class DayMark:
    day: date
    status: str  # PRESENT | ABSENT | HALF_DAY | LEAVE | UNMARKED
    paid_leave: bool = False


def lop_days(marks: list[DayMark], workable: list[date]) -> Decimal:
    by_day = {mark.day: mark for mark in marks}
    total = ZERO
    for day in workable:
        mark = by_day.get(day)
        if mark is None or mark.status == "UNMARKED":
            total += Decimal("1")
            continue
        status = mark.status.upper()
        if status == "PRESENT":
            continue
        if status == "HALF_DAY":
            total += Decimal("0.5")
            continue
        if status == "LEAVE" and mark.paid_leave:
            continue
        if status == "ABSENT" or status == "LEAVE":
            total += Decimal("1")
    return total


@dataclass(frozen=True)
class ComponentDef:
    code: str
    name: str
    kind: str  # EARNING | DEDUCTION
    calc_type: str  # FIXED | PERCENT | FORMULA
    amount: Decimal = ZERO
    percent: Decimal | None = None
    percent_of: str | None = None
    formula: str | None = None
    taxable: bool = True
    pf_wage: bool = False
    esi_wage: bool = True
    prorate: bool = True
    display_order: int = 0


class FormulaError(ValueError):
    pass


_ALLOWED_BINOP = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b if b != 0 else (_ for _ in ()).throw(FormulaError("Division by zero")),
}
_ALLOWED_UNARY = {ast.UAdd: lambda a: a, ast.USub: lambda a: -a}


def eval_formula(expression: str, variables: dict[str, Decimal]) -> Decimal:
    """Arithmetic over component codes. No names, calls, or attributes."""
    if not expression or len(expression) > 200:
        raise FormulaError("Formula is empty or too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaError("Formula could not be read") from exc

    def _walk(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return _walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, Decimal)):
            return D(node.value)
        if isinstance(node, ast.Name):
            key = node.id.upper()
            if key not in variables:
                raise FormulaError(f"Unknown component {key}")
            return D(variables[key])
        if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOP:
            return _ALLOWED_BINOP[type(node.op)](_walk(node.left), _walk(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
            return _ALLOWED_UNARY[type(node.op)](_walk(node.operand))
        raise FormulaError("Formula may only use numbers, component codes, and + - * /")

    return _walk(tree)


def component_amount(component: ComponentDef, variables: dict[str, Decimal]) -> Decimal:
    if component.calc_type == "FIXED":
        return D(component.amount)
    if component.calc_type == "PERCENT":
        base_code = (component.percent_of or "BASIC").upper()
        if base_code not in variables:
            raise FormulaError(f"Percent base {base_code} is not defined yet")
        return D(variables[base_code]) * _rate(component.percent or ZERO)
    if component.calc_type == "FORMULA":
        return eval_formula(component.formula or "0", variables)
    raise FormulaError(f"Unknown calculation {component.calc_type}")


@dataclass
class PayLine:
    code: str
    name: str
    kind: str
    amount: Decimal
    display_order: int = 0


@dataclass
class PayResult:
    lines: list[PayLine] = field(default_factory=list)
    working_days: Decimal = ZERO
    lop_days: Decimal = ZERO
    paid_days: Decimal = ZERO
    gross: Decimal = ZERO
    deductions: Decimal = ZERO
    net: Decimal = ZERO
    basic: Decimal = ZERO
    hra: Decimal = ZERO
    da: Decimal = ZERO
    pf: PfResult = field(default_factory=lambda: provident_fund(PfInput(enrolled=False, basic=ZERO)))
    esi: EsiResult = field(default_factory=lambda: esi(EsiInput(enrolled=False, gross=ZERO)))
    pt: Decimal = ZERO
    tds: TdsResult | None = None
    form16: dict | None = None


@dataclass(frozen=True)
class PayContext:
    components: list[ComponentDef]
    period_start: date
    period_end: date
    weekly: list[WeeklyOff]
    holidays: list[Holiday]
    marks: list[DayMark]
    branch_name: str | None = None
    pf: PfInput | None = None
    esi_threshold: Decimal = Decimal("21000")
    esi_employee_rate: Decimal = Decimal("0.75")
    esi_employer_rate: Decimal = Decimal("3.25")
    esi_enrolled: bool = False
    esi_from: date | None = None
    esi_to: date | None = None
    pt_slabs: list[PtSlab] = field(default_factory=list)
    pt_state: str = "KA"
    gender: str = "OTHER"
    regime: str = "NEW"
    metro: bool = False
    annual_rent: Decimal = ZERO
    declared_80c: Decimal = ZERO
    declared_80d: Decimal = ZERO
    other_chapter_vi: Decimal = ZERO
    lop_base: str = "BASIC"  # BASIC | GROSS
    rounding: str = "NEAREST_RUPEE"
    arrears: Decimal = ZERO
    reimbursements: Decimal = ZERO
    loan_emi: Decimal = ZERO
    bonus: Decimal = ZERO
    extra_deductions: Decimal = ZERO
    employee_name: str = ""
    pan: str | None = None


def compute_pay(ctx: PayContext) -> PayResult:
    days = working_days(ctx.period_start, ctx.period_end, ctx.weekly, ctx.holidays, ctx.branch_name)
    workable = Decimal(len(days))
    lost = lop_days(ctx.marks, days) if days else ZERO
    if lost > workable:
        lost = workable
    paid = workable - lost
    ordered = sorted(ctx.components, key=lambda c: (c.display_order, c.code))
    variables: dict[str, Decimal] = {
        "WORKING_DAYS": workable,
        "LOP_DAYS": lost,
        "PAID_DAYS": paid,
    }
    lines: list[PayLine] = []
    for component in ordered:
        code = component.code.upper()
        if code in RESERVED_CODES:
            raise FormulaError(f"{code} is reserved for statutory lines")
        raw = component_amount(component, variables)
        if raw < 0 and component.kind == "EARNING":
            raw = ZERO
        amount = round_money(raw, ctx.rounding)
        variables[code] = amount
        lines.append(
            PayLine(code, component.name, component.kind.upper(), amount, component.display_order)
        )
    earnings = [line for line in lines if line.kind == "EARNING"]
    custom = [line for line in lines if line.kind == "DEDUCTION"]
    gross_full = sum((line.amount for line in earnings), ZERO)
    prorated_codes = {c.code.upper() for c in ordered if c.prorate and c.kind.upper() == "EARNING"}
    if ctx.lop_base == "GROSS":
        lop_base_amount = sum((variables[c] for c in prorated_codes), ZERO)
    else:
        lop_base_amount = variables.get("BASIC", ZERO) if "BASIC" in prorated_codes or "BASIC" in variables else ZERO
        if ctx.lop_base == "BASIC":
            lop_base_amount = variables.get("BASIC", ZERO)
    if workable > 0 and lost > 0:
        lop_amount = round_money(lop_base_amount / workable * lost, ctx.rounding)
    else:
        lop_amount = ZERO
    if lop_amount > gross_full:
        lop_amount = gross_full
    basic = variables.get("BASIC", ZERO)
    da = variables.get("DA", ZERO)
    hra = variables.get("HRA", ZERO)
    pf_spec = ctx.pf or PfInput(enrolled=False, basic=basic, da=da, rounding=ctx.rounding)
    # DA is included in PF wage only when the DA component is flagged pf_wage.
    da_for_pf = da if any(c.code.upper() == "DA" and c.pf_wage for c in ordered) else ZERO
    pf_result = provident_fund(
        PfInput(
            enrolled=pf_spec.enrolled,
            basic=basic,
            da=da_for_pf,
            employee_rate=pf_spec.employee_rate,
            employer_rate=pf_spec.employer_rate,
            eps_rate=pf_spec.eps_rate,
            ceiling=pf_spec.ceiling,
            mode=pf_spec.mode,
            as_of=ctx.period_end,
            effective_from=pf_spec.effective_from,
            effective_to=pf_spec.effective_to,
            vpf_rate=pf_spec.vpf_rate,
            vpf_amount=pf_spec.vpf_amount,
            rounding=ctx.rounding,
        )
    )
    esi_gross = sum(
        (variables[c.code.upper()] for c in ordered if c.esi_wage and c.kind.upper() == "EARNING"),
        ZERO,
    ) + D(ctx.arrears) + D(ctx.bonus) + D(ctx.reimbursements)
    esi_result = esi(
        EsiInput(
            enrolled=ctx.esi_enrolled,
            gross=esi_gross,
            threshold=ctx.esi_threshold,
            employee_rate=ctx.esi_employee_rate,
            employer_rate=ctx.esi_employer_rate,
            as_of=ctx.period_end,
            effective_from=ctx.esi_from,
            effective_to=ctx.esi_to,
            rounding=ctx.rounding,
        )
    )
    taxable_monthly = sum(
        (variables[c.code.upper()] for c in ordered if c.taxable and c.kind.upper() == "EARNING"),
        ZERO,
    )
    taxable_monthly = taxable_monthly - lop_amount
    if taxable_monthly < 0:
        taxable_monthly = ZERO
    pt_amount = professional_tax(
        ctx.pt_slabs,
        state_code=ctx.pt_state,
        monthly_gross=gross_full - lop_amount if gross_full > lop_amount else ZERO,
        month=ctx.period_end.month,
        gender=ctx.gender,
        rounding=ctx.rounding,
    )
    tax = tds(
        TdsInput(
            regime=ctx.regime,
            monthly_taxable_earnings=taxable_monthly,
            monthly_basic=basic,
            monthly_hra=hra,
            monthly_employee_pf=pf_result.employee,
            monthly_pt=pt_amount,
            annual_rent=ctx.annual_rent,
            metro=ctx.metro,
            declared_80c=ctx.declared_80c,
            declared_80d=ctx.declared_80d,
            other_chapter_vi=ctx.other_chapter_vi,
            one_time_taxable=D(ctx.arrears) + D(ctx.bonus),
            rounding=ctx.rounding,
        )
    )
    if D(ctx.arrears) > 0:
        lines.append(PayLine("ARREARS", "Arrears", "EARNING", round_money(ctx.arrears, ctx.rounding), 80))
    if D(ctx.bonus) > 0:
        lines.append(PayLine("BONUS", "Statutory bonus", "EARNING", round_money(ctx.bonus, ctx.rounding), 81))
    if D(ctx.reimbursements) > 0:
        lines.append(
            PayLine("REIMBURSEMENT", "Reimbursement", "EARNING", round_money(ctx.reimbursements, ctx.rounding), 82)
        )
    if lop_amount > 0:
        lines.append(PayLine("LOP", "Loss of pay", "DEDUCTION", lop_amount, 90))
    if pf_result.employee > 0:
        lines.append(PayLine("PF", "Provident fund (employee)", "DEDUCTION", pf_result.employee, 91))
    if pf_result.vpf > 0:
        lines.append(PayLine("VPF", "Voluntary provident fund", "DEDUCTION", pf_result.vpf, 92))
    if esi_result.employee > 0:
        lines.append(PayLine("ESI", "ESI (employee)", "DEDUCTION", esi_result.employee, 93))
    if pt_amount > 0:
        lines.append(PayLine("PT", "Professional tax", "DEDUCTION", pt_amount, 94))
    if tax.monthly_tds > 0:
        lines.append(PayLine("TDS", "Tax deducted at source", "DEDUCTION", tax.monthly_tds, 95))
    if D(ctx.loan_emi) > 0:
        lines.append(PayLine("LOAN", "Loan / advance recovery", "DEDUCTION", round_money(ctx.loan_emi, ctx.rounding), 96))
    if D(ctx.extra_deductions) > 0:
        lines.append(
            PayLine("OTHER_DED", "Other deduction", "DEDUCTION", round_money(ctx.extra_deductions, ctx.rounding), 97)
        )
    if pf_result.employer > 0:
        lines.append(PayLine("PF_EMPLOYER", "Provident fund (employer)", "EMPLOYER", pf_result.employer, 110))
        lines.append(PayLine("EPS", "Employee pension scheme", "EMPLOYER", pf_result.eps, 111))
    if esi_result.employer > 0:
        lines.append(PayLine("ESI_EMPLOYER", "ESI (employer)", "EMPLOYER", esi_result.employer, 112))

    gross = sum((line.amount for line in lines if line.kind == "EARNING"), ZERO)
    deductions = sum((line.amount for line in lines if line.kind == "DEDUCTION"), ZERO)
    # Custom deduction components were already included in `lines` and therefore in `deductions`.
    net = gross - deductions
    if net < 0:
        net = ZERO
    fy = financial_year_label(ctx.period_end)
    return PayResult(
        lines=lines,
        working_days=workable,
        lop_days=lost,
        paid_days=paid,
        gross=round_money(gross, ctx.rounding),
        deductions=round_money(deductions, ctx.rounding),
        net=round_money(net, ctx.rounding),
        basic=basic,
        hra=hra,
        da=da,
        pf=pf_result,
        esi=esi_result,
        pt=pt_amount,
        tds=tax,
        form16=form16_lines(tax, employee_name=ctx.employee_name, pan=ctx.pan, financial_year=fy),
    )


def financial_year_label(day: date) -> str:
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def statutory_bonus(
    *,
    enabled: bool,
    month: int,
    bonus_month: int,
    basic: Decimal,
    da: Decimal,
    percent: Decimal = Decimal("8.33"),
    wage_ceiling: Decimal = Decimal("21000"),
    calc_ceiling: Decimal = Decimal("7000"),
    rounding: str = "NEAREST_RUPEE",
) -> Decimal:
    """Payment of Bonus Act style amount, only in the configured month, and only when enabled."""
    if not enabled or month != bonus_month:
        return ZERO
    wage = D(basic) + D(da)
    if wage <= 0 or wage > D(wage_ceiling):
        return ZERO
    base = min(wage, D(calc_ceiling))
    return round_money(base * _rate(percent), rounding)


def gratuity(
    *,
    basic: Decimal,
    da: Decimal,
    years: Decimal,
    min_years: Decimal = Decimal("5"),
    cap: Decimal = Decimal("2000000"),
    rounding: str = "NEAREST_RUPEE",
) -> Decimal:
    """15 days' basic+DA for every completed year, capped. Years below the minimum pay nothing."""
    if D(years) < D(min_years):
        return ZERO
    wage = D(basic) + D(da)
    amount = wage * 15 / Decimal("26") * D(years)
    if amount > D(cap):
        amount = D(cap)
    if amount < 0:
        amount = ZERO
    return round_money(amount, rounding)


def completed_years(joining: date | None, last_day: date) -> Decimal:
    if joining is None or last_day < joining:
        return ZERO
    days = (last_day - joining).days
    return (Decimal(days) / Decimal("365")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def loan_split(*, balance: Decimal, emi: Decimal, annual_rate: Decimal, rounding: str = "NEAREST_RUPEE") -> tuple[Decimal, Decimal, Decimal]:
    """Return (interest, principal, new_balance) for one month of reducing-balance EMI."""
    balance = D(balance)
    if balance <= 0 or emi <= 0:
        return ZERO, ZERO, balance if balance > 0 else ZERO
    interest = round_money(balance * _rate(annual_rate) / 12, rounding) if annual_rate else ZERO
    if interest > D(emi):
        return interest, ZERO, round_money(balance, rounding)
    principal = min(D(emi) - interest, balance)
    principal = round_money(principal, rounding)
    new_balance = balance - principal
    if new_balance < 0:
        new_balance = ZERO
    return interest, principal, round_money(new_balance, rounding)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlmb = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * earth * asin(sqrt(a))


def within_geofence(
    *,
    enabled: bool,
    lat: float | None,
    lng: float | None,
    centre_lat: float | None,
    centre_lng: float | None,
    radius_m: int,
) -> str:
    """Return INSIDE, OUTSIDE, NOT_CAPTURED, or DISABLED."""
    if not enabled:
        return "DISABLED"
    if lat is None or lng is None:
        return "NOT_CAPTURED"
    if centre_lat is None or centre_lng is None:
        return "NOT_CAPTURED"
    distance = haversine_m(lat, lng, centre_lat, centre_lng)
    return "INSIDE" if distance <= max(radius_m, 0) else "OUTSIDE"


def approval_transition(
    *,
    status: str,
    action: str,
    actor_id: str,
    submitter_id: str | None,
    actor_can_approve: bool,
    require_different_user: bool = True,
) -> str:
    """Maker-checker state machine. Raises ValueError with a user-facing reason."""
    status = status.upper()
    action = action.upper()
    if action == "SUBMIT":
        if status not in {"DRAFT", "REJECTED"}:
            raise ValueError("Only a draft or rejected run can be submitted")
        return "PENDING_APPROVAL"
    if action == "APPROVE":
        if status != "PENDING_APPROVAL":
            raise ValueError("Only a run waiting for approval can be approved")
        if not actor_can_approve:
            raise ValueError("You cannot approve payroll")
        if require_different_user and submitter_id and actor_id == submitter_id:
            raise ValueError("The person who submitted this run cannot approve it")
        return "APPROVED"
    if action == "REJECT":
        if status != "PENDING_APPROVAL":
            raise ValueError("Only a run waiting for approval can be rejected")
        if not actor_can_approve:
            raise ValueError("You cannot reject payroll")
        if require_different_user and submitter_id and actor_id == submitter_id:
            raise ValueError("The person who submitted this run cannot reject it")
        return "REJECTED"
    if action == "PUBLISH":
        if status not in {"APPROVED", "PENDING_APPROVAL"}:
            raise ValueError("Publish a run after it is approved")
        if status == "PENDING_APPROVAL":
            raise ValueError("Approve the run before publishing payslips")
        return "PUBLISHED"
    if action == "LOCK":
        if status not in {"APPROVED", "PUBLISHED", "PAID"}:
            raise ValueError("Lock a run only after approval")
        return "LOCKED"
    raise ValueError("Unknown payroll action")


# Seed slabs. max_amount is inclusive. Half-year slabs use half-year income.
SEEDED_PT_SLABS: list[PtSlab] = [
    PtSlab("KA", "ANY", Decimal("0"), Decimal("24999.99"), Decimal("0"), None),
    PtSlab("KA", "ANY", Decimal("25000"), None, Decimal("200"), Decimal("300")),
    PtSlab("MH", "MALE", Decimal("0"), Decimal("7500"), Decimal("0"), None),
    PtSlab("MH", "MALE", Decimal("7500.01"), Decimal("10000"), Decimal("175"), None),
    PtSlab("MH", "MALE", Decimal("10000.01"), None, Decimal("200"), Decimal("300")),
    PtSlab("MH", "FEMALE", Decimal("0"), Decimal("25000"), Decimal("0"), None),
    PtSlab("MH", "FEMALE", Decimal("25000.01"), None, Decimal("200"), Decimal("300")),
    PtSlab("MH", "OTHER", Decimal("0"), Decimal("7500"), Decimal("0"), None),
    PtSlab("MH", "OTHER", Decimal("7500.01"), Decimal("10000"), Decimal("175"), None),
    PtSlab("MH", "OTHER", Decimal("10000.01"), None, Decimal("200"), Decimal("300")),
    PtSlab("TN", "ANY", Decimal("0"), Decimal("21000"), Decimal("0"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
    PtSlab("TN", "ANY", Decimal("21000.01"), Decimal("30000"), Decimal("180"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
    PtSlab("TN", "ANY", Decimal("30000.01"), Decimal("45000"), Decimal("425"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
    PtSlab("TN", "ANY", Decimal("45000.01"), Decimal("60000"), Decimal("930"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
    PtSlab("TN", "ANY", Decimal("60000.01"), Decimal("75000"), Decimal("1025"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
    PtSlab("TN", "ANY", Decimal("75000.01"), None, Decimal("1250"), None, "HALF_YEARLY", "HALF_YEAR_GROSS"),
]
