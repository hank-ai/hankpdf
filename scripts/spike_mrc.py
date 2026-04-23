#!/usr/bin/env python3
"""Phase-1 MRC spike: compress a real PDF via the full engine pipeline.

Reads an input PDF, processes each page through rasterize -> OCR -> mask ->
foreground/background extract -> MRC compose -> invisible OCR text layer,
merges the per-page outputs, and writes a single output PDF. Prints a
compression ratio report.

Not production code. This is the feasibility gate for Phase 2.

Usage:
    python scripts/spike_mrc.py INPUT.pdf -o OUTPUT.pdf
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium

from pdf_smasher.engine.background import extract_background
from pdf_smasher.engine.compose import compose_mrc_page
from pdf_smasher.engine.foreground import extract_foreground
from pdf_smasher.engine.mask import build_mask
from pdf_smasher.engine.ocr import tesseract_word_boxes
from pdf_smasher.engine.rasterize import rasterize_page
from pdf_smasher.engine.text_layer import add_text_layer


def _page_size_pt(pdf_bytes: bytes, page_index: int) -> tuple[float, float]:
    """Return (width_pt, height_pt) for the given page."""
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        w, h = pdf[page_index].get_size()
        return float(w), float(h)
    finally:
        pdf.close()


def _page_count(pdf_bytes: bytes) -> int:
    pdf = pdfium.PdfDocument(pdf_bytes)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _compress_page(
    pdf_bytes: bytes,
    *,
    page_index: int,
    source_dpi: int,
    bg_target_dpi: int,
    ocr_language: str,
) -> bytes:
    """Compress a single page. Returns 1-page PDF bytes."""
    width_pt, height_pt = _page_size_pt(pdf_bytes, page_index)

    raster = rasterize_page(pdf_bytes, page_index=page_index, dpi=source_dpi)
    word_boxes = tesseract_word_boxes(raster, language=ocr_language)
    mask = build_mask(raster)
    fg = extract_foreground(raster, mask=mask)
    bg = extract_background(
        raster,
        mask=mask,
        source_dpi=source_dpi,
        target_dpi=bg_target_dpi,
    )
    composed = compose_mrc_page(
        foreground=fg.image,
        foreground_color=fg.ink_color,
        mask=mask,
        background=bg,
        page_width_pt=width_pt,
        page_height_pt=height_pt,
    )
    return add_text_layer(
        composed,
        page_index=0,
        word_boxes=word_boxes,
        raster_width_px=raster.size[0],
        raster_height_px=raster.size[1],
        page_width_pt=width_pt,
        page_height_pt=height_pt,
    )


def _merge_pages(page_pdfs: list[bytes]) -> bytes:
    """Stitch a list of 1-page PDFs into a single multi-page PDF."""
    out = pikepdf.new()
    for page_bytes in page_pdfs:
        src = pikepdf.open(io.BytesIO(page_bytes))
        try:
            out.pages.extend(src.pages)
        finally:
            src.close()
    buf = io.BytesIO()
    out.save(buf, linearize=False, deterministic_id=True)
    return buf.getvalue()


def run(
    input_path: Path,
    output_path: Path,
    *,
    source_dpi: int,
    bg_target_dpi: int,
    ocr_language: str,
) -> dict:
    t0 = time.monotonic()
    pdf_in = input_path.read_bytes()
    n_pages = _page_count(pdf_in)

    print(f"Input:  {input_path}  ({len(pdf_in):,} bytes, {n_pages} pages)")
    print(
        f"Settings: source_dpi={source_dpi}, bg_target_dpi={bg_target_dpi}, "
        f"ocr_language={ocr_language!r}",
    )

    page_pdfs: list[bytes] = []
    for i in range(n_pages):
        t_page = time.monotonic()
        print(f"  page {i + 1}/{n_pages} ... ", end="", flush=True)
        page_out = _compress_page(
            pdf_in,
            page_index=i,
            source_dpi=source_dpi,
            bg_target_dpi=bg_target_dpi,
            ocr_language=ocr_language,
        )
        page_pdfs.append(page_out)
        dt = time.monotonic() - t_page
        print(f"{len(page_out):,} bytes ({dt:.2f}s)")

    pdf_out = _merge_pages(page_pdfs)
    output_path.write_bytes(pdf_out)

    wall = time.monotonic() - t0
    ratio = len(pdf_in) / max(1, len(pdf_out))
    report = {
        "input_path": str(input_path),
        "output_path": str(output_path),
        "input_bytes": len(pdf_in),
        "output_bytes": len(pdf_out),
        "ratio": ratio,
        "pages": n_pages,
        "wall_time_s": wall,
        "source_dpi": source_dpi,
        "bg_target_dpi": bg_target_dpi,
    }
    print(
        f"\nOutput: {output_path}  ({len(pdf_out):,} bytes, ratio {ratio:.2f}x, "
        f"{wall:.1f}s wall, {wall / max(1, n_pages):.1f}s/page)",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input PDF path")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output PDF path",
    )
    parser.add_argument("--source-dpi", type=int, default=200)
    parser.add_argument("--bg-target-dpi", type=int, default=150)
    parser.add_argument("--ocr-language", default="eng")
    args = parser.parse_args(argv)

    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)

    run(
        args.input,
        args.output,
        source_dpi=args.source_dpi,
        bg_target_dpi=args.bg_target_dpi,
        ocr_language=args.ocr_language,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
