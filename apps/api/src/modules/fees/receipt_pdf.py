"""Minimal PDF builder for fee receipts."""

from __future__ import annotations


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _half(rate) -> str:
    try:
        return f"{float(rate) / 2:g}"
    except (TypeError, ValueError):
        return "0"


def build_receipt_pdf(payload: dict) -> bytes:
    lines = [
        str(payload.get("school_name") or "School").upper(),
        "",
        "FEE RECEIPT",
        str(payload.get("copy_label") or ""),
        "",
        f"Receipt: {payload.get('receipt_number') or '-'}",
        f"Payment: {payload.get('payment_number') or '-'}",
        f"Invoice: {payload.get('invoice_number') or '-'}",
        "",
        f"Student: {payload.get('student_name') or '-'}",
        f"Class: {payload.get('class_name') or '-'}",
        "",
        f"Amount: {payload.get('currency') or 'INR'} {payload.get('amount') or '0.00'}",
        f"Method: {payload.get('method') or '-'}",
        f"Date: {payload.get('paid_at') or '-'}",
        "",
        f"Status: {payload.get('status') or 'SUCCESS'}",
    ]
    if payload.get("gst_mode"):  # tax identity block, present once the school has a fee profile
        lines += ["", f"GSTIN: {payload.get('gstin') or '-'}   SAC: {payload.get('sac_code') or '-'}"]
        if payload.get("gst_mode") == "TAXABLE":
            lines += [
                f"Taxable value: INR {payload.get('taxable')}",
                f"CGST ({_half(payload.get('gst_rate'))}%): INR {payload.get('cgst')}",
                f"SGST ({_half(payload.get('gst_rate'))}%): INR {payload.get('sgst')}",
            ]
        else:
            lines += ["GST: exempt (educational services). No tax charged."]
        if payload.get("footer"):
            lines += ["", str(payload["footer"])]
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
