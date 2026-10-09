"""CSV/PDF exporters for tabular reports."""

from __future__ import annotations

import csv
import io


def rows_to_csv(rows: list[dict], fieldnames: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in fieldnames})
    return buf.getvalue()


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("(", "\\(")
        .replace(")", "\\)")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def rows_to_pdf(title: str, rows: list[dict], fieldnames: list[str]) -> bytes:
    lines = [title.upper(), "", " | ".join(fieldnames), "-" * 60]
    for r in rows[:80]:
        lines.append(" | ".join(str(r.get(f, ""))[:18] for f in fieldnames))
    if len(rows) > 80:
        lines.append(f"... and {len(rows) - 80} more rows")
    y = 800
    ops = ["BT", "/F1 10 Tf", "12 TL"]
    for i, line in enumerate(lines):
        esc = _escape(line)
        if i == 0:
            ops.append(f"/F1 14 Tf 40 {y} Td ({esc}) Tj")
            ops.append("/F1 10 Tf")
        else:
            ops.append(f"0 -14 Td ({esc}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", errors="replace")
    objects: list[bytes] = [
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
