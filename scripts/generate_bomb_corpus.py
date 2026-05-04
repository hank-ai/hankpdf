"""Deterministically generate decompression-bomb fixtures.

Run: python scripts/generate_bomb_corpus.py tests/corpus/bombs/

Outputs:
  huge_page_dimensions.pdf  — 60000x20000 pt MediaBox (1.2B pixel raster at 72 DPI)
  xref_loop.pdf             — xref entry pointing to itself
  objstm_explosion.pdf      — 10,001-page PDF (trips max-pages gate)
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _huge_page_dimensions(out: Path) -> None:
    body = (
        b"%PDF-1.7\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 60000 20000]>>endobj\n"
    )
    xref_offset = len(body)
    xref = (
        b"xref\n0 4\n0000000000 65535 f\n"
        + b"0000000009 00000 n\n"
        + b"0000000056 00000 n\n"
        + b"0000000108 00000 n\n"
    )
    trailer = (
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    out.write_bytes(body + xref + trailer)


def _xref_loop(out: Path) -> None:
    body = b"%PDF-1.7\n%intentional xref-loop bomb\n"
    xref_offset = len(body)
    xref = (
        b"xref\n0 1\n"
        + f"{xref_offset:010d} 65535 n\n".encode()
    )
    trailer = (
        b"trailer<</Size 1/Root 1 0 R>>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    out.write_bytes(body + xref + trailer)


def _objstm_explosion(out: Path) -> None:
    """Structurally-valid PDF with 10,001 pages → trips max-pages gate.

    Saved with object-stream compression so the fixture stays under 1 MB
    (otherwise the page-tree blows out to ~2.3 MB and we'd have to ship
    it on-demand instead of in-tree).
    """
    import pikepdf

    pdf = pikepdf.new()
    for _ in range(10_001):
        pdf.add_blank_page(page_size=(612, 792))
    pdf.save(
        out,
        compress_streams=True,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
    )


def _length_mismatch(out: Path) -> None:
    """Stream object with /Length 2_000_000_000 but ~100 MB actual data.

    Generated on-demand only via --include-large (too big to commit).
    """
    declared = 2_000_000_000
    actual = b"\x00" * (100 * 1024 * 1024)
    body = b"%PDF-1.7\n"
    obj = (
        f"1 0 obj<</Length {declared}>>stream\n".encode()
        + actual
        + b"\nendstream\nendobj\n"
    )
    body += obj
    xref_offset = len(body)
    xref = b"xref\n0 2\n0000000000 65535 f\n0000000009 00000 n\n"
    trailer = (
        b"trailer<</Size 2/Root 1 0 R>>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF\n"
    )
    out.write_bytes(body + xref + trailer)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("out_dir", type=Path)
    p.add_argument(
        "--include-large",
        action="store_true",
        help="Also generate length_mismatch.pdf (~100 MB).",
    )
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _huge_page_dimensions(args.out_dir / "huge_page_dimensions.pdf")
    _xref_loop(args.out_dir / "xref_loop.pdf")
    _objstm_explosion(args.out_dir / "objstm_explosion.pdf")
    if args.include_large:
        _length_mismatch(args.out_dir / "length_mismatch.pdf")
    print(f"wrote bomb fixtures to {args.out_dir}")


if __name__ == "__main__":
    main()
