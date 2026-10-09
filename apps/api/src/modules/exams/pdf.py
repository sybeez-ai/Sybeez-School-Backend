"""Minimal PDF builder (no external deps) for report cards."""

from __future__ import annotations


def _escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def build_report_card_pdf(payload: dict) -> bytes:
    lines: list[str] = [
        str(payload.get("school_name") or "School").upper(),
        "",
        str(payload.get("exam_title") or "Examination").upper(),
        "",
        f"Student: {payload.get('student_name') or '-'}",
        f"Class: {payload.get('class_name') or '-'}",
        f"Roll No: {payload.get('roll_number') or '-'}",
        "",
        f"{'Subject':<22}{'Max':>6}{'Marks':>8}{'Grade':>8}",
        "-" * 44,
    ]
    for s in payload.get("subjects") or []:
        marks = s.get("marks")
        if s.get("attendance") and s["attendance"] != "PRESENT":
            marks_txt = s["attendance"][:6]
        else:
            marks_txt = "-" if marks is None else str(marks)
        lines.append(
            f"{str(s.get('name') or '-')[:22]:<22}{s.get('max') or 0:>6}{marks_txt:>8}{str(s.get('grade') or '-'):>8}"
        )
    lines.extend(
        [
            "-" * 44,
            f"{'Total':<22}{payload.get('total_max') or 0:>6}{payload.get('total_obtained') or 0:>8}",
            f"Percentage: {payload.get('percentage') if payload.get('percentage') is not None else '-'}%",
            f"Grade: {payload.get('grade_label') or '-'}",
            f"Result: {payload.get('pass_fail') or '-'}",
        ]
    )

    # PDF coordinate system: start near top of A4
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
