"""Plain multi-page text PDF (built-in Courier font, no external dependencies)."""

from __future__ import annotations

import textwrap

from src.modules.hr.payslip_pdf import _escape

_PAGE_W, _PAGE_H = 595, 842
_MARGIN_X, _TOP, _BOTTOM = 56, 790, 60
_FONT_SIZE, _LEADING = 10, 14
_WRAP = 78
_LINES_PER_PAGE = (_TOP - _BOTTOM) // _LEADING

# The built-in fonts use WinAnsi; map common typography so it doesn't print as "?".
_ASCII = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        "₹": "INR ",
        "•": "*",
    }
)


def _wrap(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        text = line.translate(_ASCII).rstrip()
        if not text:
            out.append("")
            continue
        out.extend(
            textwrap.wrap(text, _WRAP, replace_whitespace=False, drop_whitespace=True)
            or [""]
        )
    return out


def build_text_pdf(lines: list[str]) -> bytes:
    wrapped = _wrap(lines) or [""]
    pages = [
        wrapped[i : i + _LINES_PER_PAGE]
        for i in range(0, len(wrapped), _LINES_PER_PAGE)
    ]

    font_id = 3 + 2 * len(pages)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
            + f"] /Count {len(pages)} >>"
        ).encode(),
    ]
    for i, page in enumerate(pages):
        ops = ["BT", f"/F1 {_FONT_SIZE} Tf", f"{_MARGIN_X} {_TOP} Td"]
        for n, line in enumerate(page):
            ops.append(
                f"({_escape(line)}) Tj"
                if n == 0
                else f"0 -{_LEADING} Td ({_escape(line)}) Tj"
            )
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", errors="replace")
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                f"/Contents {4 + 2 * i} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        objects.append(
            f"<< /Length {len(stream)} >>stream\n".encode() + stream + b"\nendstream"
        )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>"
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{n} 0 obj".encode() + body + b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)
