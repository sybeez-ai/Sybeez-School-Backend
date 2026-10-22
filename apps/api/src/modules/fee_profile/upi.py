"""Pure helpers: UPI payment link, QR, GST breakup. No gateway involved."""

from __future__ import annotations

import io
import re
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

import segno

VPA_RE = re.compile(r"^[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z]{2,32}$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def upi_uri(*, vpa: str, payee: str, amount: Decimal, invoice_number: str, note: str) -> str:
    """NPCI UPI deep link. ``tr`` carries the invoice number so the bank statement reconciles to the invoice."""
    amt = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return (
        f"upi://pay?pa={quote(vpa, safe='@')}&pn={quote(payee[:60])}&am={amt}&cu=INR"
        f"&tr={quote(invoice_number[:35])}&tn={quote(note[:50])}"
    )


def qr_svg(data: str) -> str:
    buf = io.BytesIO()
    segno.make(data, error="m").save(buf, kind="svg", scale=4, border=2, xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


def gst_breakup(amount: Decimal, mode: str, rate: Decimal) -> dict:
    """Receipt amount is the gross paid. EXEMPT: no tax. TAXABLE: amount is tax-inclusive, split out CGST/SGST halves."""
    amount = Decimal(amount)
    if mode != "TAXABLE" or rate <= 0:
        return {"taxable": amount, "cgst": Decimal("0.00"), "sgst": Decimal("0.00"), "total": amount, "mode": "EXEMPT"}
    taxable = (amount * 100 / (100 + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tax = amount - taxable
    cgst = (tax / 2).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"taxable": taxable, "cgst": cgst, "sgst": tax - cgst, "total": amount, "mode": "TAXABLE"}
