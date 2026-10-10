"""Payslip PDFs. Three built-in templates. Colours come from the school settings."""

from __future__ import annotations


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _rgb(hex_color: str) -> str:
    color = (hex_color or "#1B4F72").lstrip("#")
    if len(color) != 6:
        color = "1B4F72"
    r, g, b = int(color[0:2], 16) / 255, int(color[2:4], 16) / 255, int(color[4:6], 16) / 255
    return f"{r:.3f} {g:.3f} {b:.3f} rg"


def build_payslip_pdf(payload: dict) -> bytes:
    template = (payload.get("template_code") or "CLASSIC").upper()
    lines = _lines(payload, template)
    y = 800
    ops = ["BT", "/F1 11 Tf", "14 TL", _rgb(payload.get("primary_color"))]
    for index, line in enumerate(lines):
        escaped = _escape(line)
        if index == 0:
            ops.append(f"/F1 16 Tf 50 {y} Td ({escaped}) Tj")
            ops.append("/F1 11 Tf")
            ops.append(_rgb(payload.get("accent_color")))
        else:
            ops.append(f"0 -14 Td ({escaped}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n",
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n",
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n",
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n",
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def _lines(payload: dict, template: str) -> list[str]:
    school = payload.get("school_name") or "Sybeez School"
    header = [school, f"Payslip ({template.title()})", payload.get("period") or ""]
    identity = [
        f"Employee: {payload.get('employee_name') or '-'}",
        f"Code: {payload.get('employee_code') or '-'}",
    ]
    if template == "STATUTORY":
        identity.extend(
            [
                f"PAN: {payload.get('pan_masked') or '-'}",
                f"UAN: {payload.get('uan') or '-'}",
                f"Tax regime: {payload.get('tax_regime') or '-'}",
                f"PT state: {payload.get('pt_state') or '-'}",
            ]
        )
    if payload.get("show_bank_last4"):
        identity.append(f"Account: {payload.get('account_last4') or '-'}")
    body = ["Earnings"]
    for line in payload.get("earnings") or []:
        body.append(f"  {line['name']}: INR {line['amount']}")
    body.append("Deductions")
    for line in payload.get("deductions") or []:
        body.append(f"  {line['name']}: INR {line['amount']}")
    if template != "COMPACT" and payload.get("show_employer_share"):
        body.append("Employer contributions (not deducted)")
        for line in payload.get("employer") or []:
            body.append(f"  {line['name']}: INR {line['amount']}")
    if payload.get("show_lop"):
        body.append(
            f"Working days {payload.get('working_days')}  LOP {payload.get('lop_days')}  "
            f"Paid {payload.get('paid_days')}"
        )
    totals = [
        f"Gross: INR {payload.get('gross')}",
        f"Deductions: INR {payload.get('deductions')}",
        f"Net pay: INR {payload.get('net')}",
        f"Status: {payload.get('status')}",
    ]
    gap = [""] if template != "COMPACT" else []
    return header + gap + identity + gap + body + gap + totals
