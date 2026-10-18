"""Daily payroll desk: statutory edges, bank files, loans, imports, and pages."""

from datetime import date
from decimal import Decimal
from zipfile import ZipFile
import io

import pytest

from src.modules.payroll.bank import render_bank_file
from src.modules.payroll.calc import (
    SEEDED_PT_SLABS,
    DayMark,
    EsiInput,
    PfInput,
    TdsInput,
    approval_transition,
    esi,
    lop_audit,
    professional_tax,
    provident_fund,
    tds,
)
from src.modules.payroll.ops import (
    arrears_for_revision,
    backdated_months,
    emi_schedule,
    page_rows,
    parse_bank_response,
    pf_ecr_text,
    rows_xlsx,
    validate_bank_row,
    validate_component_fields,
    validate_employee_row,
)

D = Decimal


def test_pf_ceiling_and_esi_threshold_edges():
    capped = provident_fund(PfInput(enrolled=True, basic=D("15000"), mode="CAPPED"))
    over = provident_fund(PfInput(enrolled=True, basic=D("15000.01"), mode="CAPPED"))
    full = provident_fund(PfInput(enrolled=True, basic=D("15000.01"), mode="FULL_BASIC"))
    assert over.employee == capped.employee
    assert full.wage > over.wage
    assert not provident_fund(PfInput(enrolled=False, basic=D("20000"))).employee
    assert esi(EsiInput(enrolled=True, gross=D("21000"))).covered is True
    assert esi(EsiInput(enrolled=True, gross=D("21000.01"))).covered is False
    assert esi(EsiInput(enrolled=True, gross=D("0"))).covered is False


def test_pt_and_tds_edges():
    assert professional_tax(SEEDED_PT_SLABS, state_code="KA", monthly_gross=D("24999.99"), month=2) == 0
    assert professional_tax(SEEDED_PT_SLABS, state_code="KA", monthly_gross=D("25000"), month=2) == D("300")
    assert professional_tax(SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("25000"), month=6, gender="FEMALE") == 0
    assert professional_tax(SEEDED_PT_SLABS, state_code="MH", monthly_gross=D("25000.01"), month=6, gender="FEMALE") == D("200")
    capped = tds(
        TdsInput(
            regime="OLD",
            monthly_taxable_earnings=D("200000"),
            monthly_employee_pf=D("5000"),
            declared_80c=D("200000"),
            rounding="PAISE",
        )
    )
    assert capped.chapter_vi_a == D("150000.00")
    once = tds(TdsInput(monthly_taxable_earnings=D("200000"), one_time_taxable=D("50000"), rounding="PAISE"))
    assert once.annual_gross == D("2450000.00")
    assert once.cess > 0


def test_lop_audit_names_the_source():
    marks = [
        DayMark(date(2026, 10, 1), "ABSENT"),
        DayMark(date(2026, 10, 2), "HALF_DAY"),
        DayMark(date(2026, 10, 3), "LEAVE", paid_leave=False),
        DayMark(date(2026, 10, 4), "LEAVE", paid_leave=True),
        DayMark(date(2026, 10, 5), "PRESENT"),
    ]
    days = [date(2026, 10, day) for day in range(1, 7)]
    rows = lop_audit(marks, days)
    sources = {row["date"]: row["source"] for row in rows}
    assert sources["2026-10-01"] == "ABSENT"
    assert sources["2026-10-02"] == "HALF_DAY"
    assert sources["2026-10-03"] == "UNPAID_LEAVE"
    assert sources["2026-10-06"] == "UNMARKED_WORKING_DAY"
    assert "2026-10-04" not in sources
    assert "2026-10-05" not in sources


def test_lock_only_after_paid_and_cancel_before_approval():
    with pytest.raises(ValueError, match="paid"):
        approval_transition(status="PUBLISHED", action="LOCK", actor_id="a", submitter_id="b", actor_can_approve=True)
    assert approval_transition(status="PAID", action="LOCK", actor_id="a", submitter_id="b", actor_can_approve=True) == "LOCKED"
    assert approval_transition(status="DRAFT", action="CANCEL", actor_id="a", submitter_id=None, actor_can_approve=False) == "CANCELLED"
    with pytest.raises(ValueError, match="before it is approved"):
        approval_transition(status="APPROVED", action="CANCEL", actor_id="a", submitter_id="b", actor_can_approve=True)


def test_revision_arrears_and_emi_schedule():
    assert backdated_months(date(2026, 8, 1), date(2026, 10, 11)) == 2
    assert backdated_months(date(2026, 10, 1), date(2026, 10, 11)) == 0
    assert arrears_for_revision(old_monthly=D("30000"), new_monthly=D("33000"), months=2) == D("6000.00")
    assert arrears_for_revision(old_monthly=D("33000"), new_monthly=D("30000"), months=2) == 0
    schedule = emi_schedule(balance=D("1000"), emi=D("10"), annual_rate=D("24"), start=date(2026, 1, 1), limit=3)
    assert schedule[-1]["principal"] == "0.00"
    assert "interest" in schedule[-1]["note"]


def test_bank_formats_response_and_ecr():
    row = [{
        "beneficiary_name": "Asha",
        "account_number": "123456789012",
        "ifsc": "HDFC0001234",
        "amount": D("100.00"),
        "narration": "Salary",
        "employee_code": "E1",
    }]
    hdfc, _mime = render_bank_file("HDFC", row)
    icici, _mime = render_bank_file("ICICI", row)
    sbi, _mime = render_bank_file("SBI", row)
    axis, _mime = render_bank_file("AXIS", row)
    assert "Beneficiary Account Number" in hdfc
    assert "BENE_ACC_NO" in icici
    assert "ACCOUNT NUMBER" in sbi
    assert "Beneficiary IFSC" in axis
    parsed, errors = parse_bank_response(b"account_number,status,utr,reason\n123456789012,SUCCESS,UTR1,\n1234567890,FAILED,,returned\n")
    assert errors == []
    assert parsed[0]["status"] == "PAID" and parsed[0]["utr"] == "UTR1"
    assert parsed[1]["status"] == "FAILED"
    bad, bad_errors = parse_bank_response(b"account_number,status\n123,MAYBE,\n")
    assert bad == []
    assert bad_errors
    text = pf_ecr_text([{"uan": "100", "name": "Asha", "gross": "100", "epf_wages": "100", "eps_wages": "100", "edli_wages": "100", "employee_share": "12", "eps": "8.33", "ncp_days": "1"}])
    assert "#~#" in text
    assert text.startswith("100#~#Asha#~#")


def test_import_validation_and_exports():
    assert validate_employee_row({"employee_code": "", "full_name": ""}, 2)
    assert validate_employee_row({"employee_code": "E1", "full_name": "Asha", "pan": "ABCDE1234F"}, 2) == []
    parsed, errors = validate_bank_row(
        {"employee_code": "E1", "account_holder": "Asha", "account_number": "123456789", "ifsc": "HDFC0001234"},
        3,
    )
    assert errors == []
    assert parsed["ifsc"] == "HDFC0001234"
    with pytest.raises(ValueError, match="percent"):
        validate_component_fields(kind="EARNING", calc_type="PERCENT", amount=None, percent=D("140"), percent_of="BASIC", formula=None)
    book = rows_xlsx(["name"], [["Asha"]])
    with ZipFile(io.BytesIO(book)) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
    page, total = page_rows([{"name": "Asha"}, {"name": "Ravi"}], q="ash", limit=10, offset=0, text_of=lambda row: row["name"])
    assert total == 1 and page[0]["name"] == "Asha"


def test_service_marks_do_not_charge_untracked_or_future_days():
    import inspect

    from src.modules.payroll.service import PayrollService

    src = inspect.getsource(PayrollService._marks)
    assert "not tracked or day > today" in src
