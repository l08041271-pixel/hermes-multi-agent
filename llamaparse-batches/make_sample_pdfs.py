#!/usr/bin/env python3
"""Generate a handful of tiny, valid one-page PDFs for testing the Batches flow.

No third-party dependencies -- the PDF is assembled by hand so this works even
when you cannot install packages (e.g. a locked-down sandbox).

Usage:
    python make_sample_pdfs.py                 # writes 3 PDFs to ./invoices
    python make_sample_pdfs.py --out ./docs --count 5
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_pdf(text_lines: list[str]) -> bytes:
    """Return a minimal single-page PDF containing the given lines of text."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content_lines = ["BT", "/F1 12 Tf", "72 720 Td", "16 TL"]
    for line in text_lines:
        content_lines.append(f"({esc(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_pos = len(out)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += b"trailer\n" + f"<< /Size {size} /Root 1 0 R >>\n".encode()
    out += b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n"
    return bytes(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="./invoices", help="output directory")
    parser.add_argument("--count", type=int, default=3, help="number of PDFs")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, args.count + 1):
        lines = [
            f"Sample Invoice #{i:03d}",
            "",
            "Vendor: Acme Widgets Inc.",
            f"Invoice number: INV-{i:04d}",
            "Date: 2026-01-01",
            "",
            f"Subtotal: ${i * 100}.00",
            "Tax: $0.00",
            f"Total due: ${i * 100}.00",
        ]
        path = out_dir / f"invoice_{i:03d}.pdf"
        path.write_bytes(build_pdf(lines))
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
