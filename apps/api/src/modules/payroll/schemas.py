"""Request bodies for school-configurable payroll."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class ReasonBody(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class SettingsUpdate(ReasonBody):
    pt_state: str | None = None
    tax_regime_default: str | None = None
    pf_employee_rate: Decimal | None = None
    pf_employer_rate: Decimal | None = None
    pf_wage_ceiling: Decimal | None = None
    pf_default_mode: str | None = None
    eps_rate: Decimal | None = None
    esi_employee_rate: Decimal | None = None
    esi_employer_rate: Decimal | None = None
    esi_wage_ceiling: Decimal | None = None
    rounding_mode: str | None = None
    lop_base: str | None = None
    pay_date_rule: str | None = None
    pay_day: int | None = Field(default=None, ge=1, le=31)
    auto_publish: bool | None = None
    bank_format: str | None = None
    template_code: str | None = None
    primary_color: str | None = None
    accent_color: str | None = None
    show_employer_share: bool | None = None
    show_lop: bool | None = None
    show_bank_last4: bool | None = None
    show_pan: bool | None = None
    shift_start: time | None = None
    shift_end: time | None = None
    grace_minutes: int | None = Field(default=None, ge=0, le=180)
    full_day_minutes: int | None = Field(default=None, ge=1, le=1440)
    half_day_minutes: int | None = Field(default=None, ge=1, le=1440)
    geofence_enabled: bool | None = None
    geofence_lat: Decimal | None = None
    geofence_lng: Decimal | None = None
    geofence_radius_m: int | None = Field(default=None, ge=10, le=50000)
    bonus_enabled: bool | None = None
    bonus_percent: Decimal | None = None
    bonus_wage_ceiling: Decimal | None = None
    bonus_calc_ceiling: Decimal | None = None
    bonus_month: int | None = Field(default=None, ge=1, le=12)
    gratuity_cap: Decimal | None = None
    gratuity_min_years: Decimal | None = None
    require_different_approver: bool | None = None
    effective_from: date | None = None


class ComponentCreate(ReasonBody):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=120)
    kind: str
    calc_type: str
    amount: Decimal | None = None
    percent: Decimal | None = None
    percent_of: str | None = None
    formula: str | None = None
    taxable: bool = True
    pf_wage: bool = False
    esi_wage: bool = True
    prorate: bool = True
    display_order: int = 0
    effective_from: date | None = None


class TemplateLineIn(BaseModel):
    component_id: UUID
    amount: Decimal | None = None
    percent: Decimal | None = None


class TemplateCreate(ReasonBody):
    name: str = Field(min_length=1, max_length=120)
    grade_code: str | None = None
    lines: list[TemplateLineIn] = Field(default_factory=list)


class SalaryAssign(ReasonBody):
    employee_id: UUID
    template_id: UUID
    effective_from: date
    overrides: dict = Field(default_factory=dict)
    arrears_amount: Decimal | None = None


class StatutoryProfileIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
    pan: str | None = None
    uan: str | None = None
    pf_number: str | None = None
    esi_number: str | None = None
    gender: str | None = None
    tax_regime: str | None = None
    metro: bool | None = None
    pt_state: str | None = None
    pf_enrolled: bool | None = None
    pf_wage_mode: str | None = None
    pf_employee_rate: Decimal | None = None
    pf_employer_rate: Decimal | None = None
    pf_effective_from: date | None = None
    pf_effective_to: date | None = None
    vpf_rate: Decimal | None = None
    vpf_amount: Decimal | None = None
    esi_enrolled: bool | None = None
    esi_effective_from: date | None = None
    esi_effective_to: date | None = None
    branch_name: str | None = Field(default=None, max_length=120)


class BankIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)
    account_holder: str = Field(min_length=1, max_length=120)
    account_number: str = Field(min_length=6, max_length=32)
    ifsc: str = Field(min_length=11, max_length=11)
    bank_name: str = Field(min_length=1, max_length=120)


class WeeklyOffIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    occurrence: str = "ALL"
    branch_name: str | None = None


class WeeklyOffReplace(ReasonBody):
    rules: list[WeeklyOffIn]


class HolidayIn(BaseModel):
    holiday_date: date
    name: str = Field(min_length=1, max_length=160)
    kind: str
    branch_name: str | None = None
    reason: str | None = Field(default=None, max_length=2000)


class PtSlabIn(BaseModel):
    min_amount: Decimal = Decimal("0")
    max_amount: Decimal | None = None
    amount: Decimal
    february_amount: Decimal | None = None
    gender: str = "ANY"
    frequency: str = "MONTHLY"
    basis: str = "MONTHLY_GROSS"


class PtReplace(ReasonBody):
    slabs: list[PtSlabIn]


class LoanIn(BaseModel):
    employee_id: UUID
    kind: str = "LOAN"
    principal: Decimal
    emi: Decimal
    annual_rate: Decimal = Decimal("0")
    start_date: date
    reason: str = Field(min_length=3, max_length=2000)


class LoanPause(ReasonBody):
    paused_until: date


class ArrearIn(BaseModel):
    employee_id: UUID
    amount: Decimal
    reason: str = Field(min_length=3, max_length=2000)


class ReimbursementIn(BaseModel):
    employee_id: UUID | None = None
    for_self: bool = False
    amount: Decimal
    note: str = Field(min_length=3, max_length=2000)


class DecisionIn(ReasonBody):
    approve: bool


class DeclarationIn(BaseModel):
    financial_year: str = Field(min_length=9, max_length=9)
    regime: str = "NEW"
    section_80c: Decimal = Decimal("0")
    section_80d: Decimal = Decimal("0")
    other_chapter_vi: Decimal = Decimal("0")
    annual_rent: Decimal = Decimal("0")
    employee_id: UUID | None = None
    reason: str | None = None


class ProofIn(BaseModel):
    section: str = Field(min_length=1, max_length=32)
    amount: Decimal
    note: str | None = None


class RoleGrantIn(BaseModel):
    user_id: UUID
    role: str
    effective_from: date | None = None
    effective_to: date | None = None
    reason: str | None = None


class ComplianceIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    due_on: date
    kind: str = "SCHOOL"
    notes: str | None = None
    reason: str | None = None


class PreviewIn(BaseModel):
    employee_id: UUID
    period_start: date
    period_end: date


class GenerateIn(BaseModel):
    period_start: date
    period_end: date
    reason: str | None = None


class RunAction(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class PaymentUpdate(BaseModel):
    status: str
    utr: str | None = None
    failure_reason: str | None = None
    reason: str | None = None


class SettlementIn(BaseModel):
    employee_id: UUID
    last_working_day: date
    leave_encashment_days: Decimal = Decimal("0")
    notice_recovery: Decimal = Decimal("0")
    reason: str | None = None


class CheckIn(BaseModel):
    lat: Decimal | None = None
    lng: Decimal | None = None
    manual: bool = False
    reason: str | None = None


class ManualAttendance(BaseModel):
    employee_id: UUID | None = None
    check_in_at: datetime
    check_out_at: datetime
    reason: str = Field(min_length=3, max_length=2000)
    lat: Decimal | None = None
    lng: Decimal | None = None


class SessionCorrect(BaseModel):
    check_in_at: datetime | None = None
    check_out_at: datetime | None = None
    reason: str = Field(min_length=3, max_length=2000)


class RegularisationIn(BaseModel):
    check_in_at: datetime
    check_out_at: datetime
    reason: str = Field(min_length=3, max_length=2000)


class RegularisationDecision(BaseModel):
    approve: bool
    note: str | None = None


class PeriodLock(BaseModel):
    period_start: date
    period_end: date
    reason: str | None = None


class InternalGenerate(BaseModel):
    school_id: UUID
    period_start: date
    period_end: date
    locked_by: UUID
    source: str = "ATTENDANCE_LOCK"


class SalaryRevision(SalaryAssign):
    compute_arrears: bool = True


class LoanPrepay(ReasonBody):
    amount: Decimal = Field(gt=0)
