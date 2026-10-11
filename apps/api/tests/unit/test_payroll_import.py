"""Tally-style pay sheet preview, header mapping, and signed report links."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.modules.payroll.assistant.downloads import open_download_token, sign_download_token
from src.modules.payroll.assistant.planner import plan_local
from src.modules.payroll.assistant.sheets import (
    SheetError,
    deterministic_mapping,
    merge_mapping,
    parse_sheet,
    tally_sample_xlsx,
    validate_sheet,
)
from src.modules.payroll.calc import TdsInput, tds
from src.modules.payroll.ops import assignment_calc
from src.core.storage import put_object

client = TestClient(app)


def test_tally_sheet_previews_one_row_and_names_every_reject():
    content = tally_sample_xlsx()
    headers, matrix = parse_sheet(content, "tally-pay-sheet.xlsx")
    mapping, unknown = deterministic_mapping(headers, {"BASIC", "HRA", "DA"})
    assert unknown == ["Bonus"]
    assert mapping["Name of Employee"] == "full_name"
    assert mapping["Employee No."] == "employee_code"
    assert mapping["A/c No."] == "account_number"
    assert mapping["IFS Code"] == "ifsc"
    assert mapping["Basic Salary"] == "BASIC"
    assert mapping["YTD Earnings"] == "ytd_gross"
    preview = validate_sheet(headers, matrix, mapping, {"BASIC", "HRA", "DA"})
    assert len(preview["accepted"]) == 1
    good = preview["accepted"][0]
    assert good["employee_code"] == "E001"
    assert good["pan"] == "ABCDE1234F"
    assert good["components"]["BASIC"] == "30000.00"
    assert good["components"]["HRA"] == "12000.00"
    assert good["ytd"]["ytd_gross"] == "180000.00"
    assert good["ytd"]["ytd_tds"] == "12000.00"
    assert good["bank"]["ifsc"] == "HDFC0001234"
    reasons = {item["reason"] for item in preview["rejected"]}
    assert "missing PAN" in reasons
    assert "invalid IFSC" in reasons
    assert "duplicate code" in reasons
    assert "unknown component: Bonus" in reasons
    assert "unreadable cell: Basic Salary" in reasons


def test_tally_html_xls_uses_the_same_columns():
    rows = [
        ["Name of Employee", "Employee No.", "PAN"],
        ["Asha Rao", "E001", "ABCDE1234F"],
    ]
    html = "<html><body><table>" + "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    ) + "</table></body></html>"
    headers, matrix = parse_sheet(html.encode(), "PaySheet.xls")
    assert headers[0] == "Name of Employee"
    assert matrix[0][1] == "E001"
    with pytest.raises(SheetError):
        parse_sheet(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1not-a-table", "legacy.xls")


def test_pdf_text_rows_and_disallowed_header_mapping():
    pdf = b"""%PDF-1.1
1 0 obj
<< /Length 88 >>
stream
BT (Name of Employee) Tj (Employee No.) Tj T* (Asha Rao) Tj (E009) Tj ET
endstream
endobj
%%EOF
"""
    headers, matrix = parse_sheet(pdf, "register.pdf")
    assert headers[:2] == ["Name of Employee", "Employee No."]
    assert matrix[0][0] == "Asha Rao"
    merged = merge_mapping({}, {"Bonus": "BONUS", "Basic Salary": "BASIC"}, {"BASIC", "HRA", "DA"})
    assert merged == {"Basic Salary": "BASIC"}


def test_amount_override_is_fixed_and_opening_tds_reduces_the_month():
    assert assignment_calc("PERCENT", {"amount": "12000"}) == "FIXED"
    assert assignment_calc("PERCENT", {"percent": "40"}) == "PERCENT"
    assert assignment_calc("PERCENT", {"amount": "1", "percent": "40"}) == "PERCENT"
    base = tds(TdsInput(monthly_taxable_earnings=Decimal("200000"), months_remaining=1))
    same = tds(TdsInput(monthly_taxable_earnings=Decimal("200000"), prior_tds=Decimal("0"), months_remaining=1))
    assert same.monthly_tds == base.monthly_tds
    assert same.annual_gross == base.annual_gross
    cleared = tds(
        TdsInput(
            monthly_taxable_earnings=Decimal("200000"),
            prior_tds=base.annual_tax,
            months_remaining=12,
        )
    )
    assert cleared.monthly_tds == 0
    bumped = tds(TdsInput(monthly_taxable_earnings=Decimal("1000"), prior_gross=Decimal("5000"), months_remaining=12))
    plain = tds(TdsInput(monthly_taxable_earnings=Decimal("1000")))
    assert bumped.annual_gross == plain.annual_gross + Decimal("5000")


def test_local_planner_prepares_report_downloads():
    assert plan_local([{"role": "user", "content": "download the salary register"}])[0].arguments["kind"] == "register"
    assert plan_local([{"role": "user", "content": "download the bank file"}])[0].arguments["kind"] == "bank"
    assert plan_local([{"role": "user", "content": "download the pf ecr"}])[0].arguments["kind"] == "pf_ecr"
    assert plan_local([{"role": "user", "content": "download form 16 for 2026-27"}])[0].arguments["financial_year"] == "2026-27"
    assert plan_local([{"role": "user", "content": "download the payslip zip"}])[0].arguments["kind"] == "payslips_zip"
    assert plan_local([{"role": "user", "content": "download the audit log"}])[0].arguments["kind"] == "audit"


def test_signed_download_expires_and_serves_the_file():
    token = sign_download_token(
        key="schools/11111111-1111-1111-1111-111111111111/assistant-downloads/one",
        filename="audit-log.csv",
        content_type="text/csv",
        school_id="11111111-1111-1111-1111-111111111111",
        ttl=60,
    )
    assert open_download_token(token)["filename"] == "audit-log.csv"
    expired = sign_download_token(
        key="schools/11111111-1111-1111-1111-111111111111/assistant-downloads/one",
        filename="audit-log.csv",
        content_type="text/csv",
        school_id="11111111-1111-1111-1111-111111111111",
        ttl=-5,
    )
    with pytest.raises(ValueError):
        open_download_token(expired)
    missing = client.get("/api/v1/payroll/assistant/downloads/not-a-token")
    assert missing.status_code == 404
    key = "schools/11111111-1111-1111-1111-111111111111/assistant-downloads/sample.csv"
    put_object(key=key, data=b"action,entity_type\n", content_type="text/csv")
    live = sign_download_token(key=key, filename="audit-log.csv", content_type="text/csv", school_id="11111111-1111-1111-1111-111111111111")
    fetched = client.get(f"/api/v1/payroll/assistant/downloads/{live}")
    assert fetched.status_code == 200
    assert fetched.text.startswith("action,entity_type")
    assert "text/csv" in fetched.headers["content-type"]
