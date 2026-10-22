from decimal import Decimal

from src.modules.fee_profile.upi import GSTIN_RE, VPA_RE, gst_breakup, qr_svg, upi_uri
from src.modules.fees.receipt_pdf import build_receipt_pdf


def test_upi_uri_and_qr():
    uri = upi_uri(vpa="school@okbank", payee="Sybeez Public School", amount=Decimal("1500"), invoice_number="INV/26/0001", note="Fees INV/26/0001")
    assert uri.startswith("upi://pay?pa=school@okbank&pn=Sybeez%20Public%20School&am=1500.00&cu=INR&tr=INV/26/0001")
    assert qr_svg(uri).lstrip().startswith("<svg")


def test_validators():
    assert VPA_RE.match("school@okhdfc") and not VPA_RE.match("not a vpa")
    assert GSTIN_RE.match("29ABCDE1234F1Z5") and not GSTIN_RE.match("29ABCDE1234F1")


def test_gst_breakup_exempt_and_taxable():
    assert gst_breakup(Decimal("1000"), "EXEMPT", Decimal("0"))["cgst"] == Decimal("0.00")
    t = gst_breakup(Decimal("1180"), "TAXABLE", Decimal("18"))
    assert t["taxable"] == Decimal("1000.00") and t["cgst"] == Decimal("90.00") and t["sgst"] == Decimal("90.00")


def test_receipt_shows_gst_block():
    base = {"school_name": "S", "receipt_number": "R1", "amount": "1180", "gst_mode": "TAXABLE", "gstin": "29ABCDE1234F1Z5",
            "sac_code": "999293", "gst_rate": "18", "taxable": "1000.00", "cgst": "90.00", "sgst": "90.00"}
    pdf = build_receipt_pdf(base)
    assert b"GSTIN: 29ABCDE1234F1Z5" in pdf and b"CGST \\(9%\\)" in pdf
    assert b"exempt" in build_receipt_pdf({**base, "gst_mode": "EXEMPT"})
