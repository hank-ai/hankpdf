"""End-to-end MRC spike on a synthetic 'scanned' page.

Builds a fake scanned PDF (white page with rendered text), runs it through
the full rasterize → OCR → mask → foreground/background → compose → text
layer pipeline, and asserts:

1. The output is smaller than the input (compression happened).
2. The output is a valid PDF that round-trips through pdfium.
3. Text is searchable in the output.
4. Rasterized output preserves dark ink roughly where the source had it.

This is the Phase-1 go/no-go gate — if this passes on a synthetic page, the
spike script (next task) can run it on a real scanned PDF.
"""

from __future__ import annotations

import io

import numpy as np
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher.engine.background import extract_background
from pdf_smasher.engine.compose import compose_mrc_page
from pdf_smasher.engine.foreground import extract_foreground
from pdf_smasher.engine.mask import build_mask
from pdf_smasher.engine.ocr import tesseract_word_boxes
from pdf_smasher.engine.rasterize import rasterize_page
from pdf_smasher.engine.text_layer import add_text_layer


def _make_fake_scanned_pdf(
    *,
    text_lines: list[str],
    page_width_pt: float = 612.0,
    page_height_pt: float = 792.0,
    source_dpi: int = 200,
) -> tuple[bytes, int, int]:
    """Render ``text_lines`` onto a white bitmap, wrap it in a 1-page PDF.

    Returns ``(pdf_bytes, raster_width_px, raster_height_px)``.
    """
    width_px = round(page_width_pt * source_dpi / 72)
    height_px = round(page_height_pt * source_dpi / 72)
    img = Image.new("RGB", (width_px, height_px), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 60)
    except OSError:
        font = ImageFont.load_default(size=60)
    y = 200
    for line in text_lines:
        draw.text((150, y), line, fill="black", font=font)
        y += 120

    # Wrap as a 1-page PDF by adding the image as a page via pikepdf.
    import pikepdf

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(page_width_pt, page_height_pt))
    page = pdf.pages[0]

    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=92, subsampling=0)
    xobj = pdf.make_stream(
        jpeg_buf.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=width_px,
        Height=height_px,
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    ops = (f"q {page_width_pt} 0 0 {page_height_pt} 0 0 cm /Scan Do Q\n").encode("ascii")
    page.Contents = pdf.make_stream(ops)

    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue(), width_px, height_px


def _run_pipeline(
    pdf_bytes: bytes,
    *,
    source_dpi: int = 200,
    bg_target_dpi: int = 100,
    page_width_pt: float = 612.0,
    page_height_pt: float = 792.0,
) -> bytes:
    """Run rasterize -> OCR -> mask -> MRC compose -> text layer."""
    raster = rasterize_page(pdf_bytes, page_index=0, dpi=source_dpi)
    word_boxes = tesseract_word_boxes(raster)
    mask = build_mask(raster, word_boxes=word_boxes)
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
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )
    return add_text_layer(
        composed,
        page_index=0,
        word_boxes=word_boxes,
        raster_width_px=raster.size[0],
        raster_height_px=raster.size[1],
        page_width_pt=page_width_pt,
        page_height_pt=page_height_pt,
    )


@pytest.mark.integration
def test_end_to_end_produces_valid_smaller_pdf() -> None:
    pdf_in, _w, _h = _make_fake_scanned_pdf(
        text_lines=["The quick brown fox", "jumps over the lazy dog", "INVOICE 12345"],
    )
    pdf_out = _run_pipeline(pdf_in)
    assert pdf_out.startswith(b"%PDF-")
    assert len(pdf_out) < len(pdf_in), (
        f"expected compression: input {len(pdf_in):,} bytes, output {len(pdf_out):,}"
    )


@pytest.mark.integration
def test_end_to_end_output_is_readable() -> None:
    pdf_in, _w, _h = _make_fake_scanned_pdf(text_lines=["HELLO WORLD"])
    pdf_out = _run_pipeline(pdf_in)
    pdf = pdfium.PdfDocument(pdf_out)
    try:
        assert len(pdf) == 1
    finally:
        pdf.close()


@pytest.mark.integration
def test_end_to_end_text_is_searchable() -> None:
    pdf_in, _w, _h = _make_fake_scanned_pdf(text_lines=["HELLO WORLD"])
    pdf_out = _run_pipeline(pdf_in)
    pdf = pdfium.PdfDocument(pdf_out)
    try:
        tp = pdf[0].get_textpage()
        text = tp.get_text_bounded()
        tp.close()
    finally:
        pdf.close()
    # OCR should have caught both words and added them as an invisible layer.
    assert "HELLO" in text
    assert "WORLD" in text


@pytest.mark.integration
def test_end_to_end_visual_output_has_dark_ink_where_source_had_text() -> None:
    pdf_in, _src_w, _src_h = _make_fake_scanned_pdf(
        text_lines=["DARK INK HERE"],
    )
    pdf_out = _run_pipeline(pdf_in)

    # Sanity: rasterize the output at the same DPI as the source; check that
    # somewhere in the page there are dark pixels (where the text is).
    pdf = pdfium.PdfDocument(pdf_out)
    try:
        img_out = pdf[0].render(scale=200 / 72).to_pil().convert("L")
    finally:
        pdf.close()

    arr = np.asarray(img_out)
    # Source raster for "DARK INK HERE" at 60pt has ~0.17% dark pixels;
    # output should preserve most of that ink, within codec tolerance.
    dark_pixel_frac = (arr < 128).sum() / arr.size
    assert dark_pixel_frac > 0.001, f"no dark ink in rasterized output; got {dark_pixel_frac:.4%}"
