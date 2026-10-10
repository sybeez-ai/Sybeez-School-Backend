"""Minimal PDF builder for payroll payslips (no statutory components)."""

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


def build_payslip_pdf(payload: dict) -> bytes:
    lines = [
        str(payload.get("school_name") or "School").upper(),
        "",
        "PAYSLIP",
        "",
        f"Payroll run: {payload.get('run_label') or '-'}",
        f"Period: {payload.get('period') or '-'}",
        "",
        f"Employee: {payload.get('teacher_name') or '-'}",
        f"Employee code: {payload.get('employee_code') or '-'}",
        "",
        f"Gross: INR {payload.get('gross_amount') or '0.00'}",
        f"Deductions: INR {payload.get('deductions_amount') or '0.00'}",
        f"Net: INR {payload.get('net_amount') or '0.00'}",
        "",
        f"Status: {payload.get('status') or 'DRAFT'}",
        f"Paid on: {payload.get('paid_on') or '-'}",
    ]
    if payload.get("notes"):
        lines.extend(["", "Notes:", str(payload.get("notes"))])

    y = 800
    content_ops: list[str] = ["BT", "/F1 11 Tf", "14 TL"]
    for i, line in enumerate(lines):
        escaped = _escape(line)
        if i == 0:
            content_ops.append(f"/F1 16 Tf 50 {y} Td ({escaped}) Tj")
            content_ops.append("/F1 11 Tf")
        else:
            content_ops.append(f"0 -16 Td ({escaped}) Tj")
    content_ops.append("ET")
    stream = "\n".join(content_ops).encode("latin-1", errors="replace")

    objects: list[bytes] = []
    objects.append(b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
    objects.append(b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
    objects.append(
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
    )
    objects.append(
        f"4 0 obj<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>endobj\n")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)

