"""Tests for hankpdf.engine.text_layer — invisible OCR text.

We add a text layer to the composed PDF so users can select + search text
that was recognized by OCR. Rendering mode 3 (invisible) means the text is
present in the PDF but not painted to the screen — downstream search +
copy/paste work, visual fidelity is unchanged.
"""

from __future__ import annotations

import io

import pikepdf
import pypdfium2 as pdfium
import pytest

from hankpdf.engine.ocr import WordBox
from hankpdf.engine.text_layer import add_text_layer, extract_native_word_boxes


def _single_page_pdf() -> bytes:
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(612.0, 792.0)
    import io

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_adds_invisible_text_and_returns_bytes() -> None:
    pdf_bytes = _single_page_pdf()
    boxes = [
        WordBox(text="HELLO", x=100, y=100, width=80, height=30, confidence=95.0),
    ]
    out = add_text_layer(
        pdf_bytes,
        page_index=0,
        word_boxes=boxes,
        raster_width_px=2550,
        raster_height_px=3300,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    assert isinstance(out, bytes)
    assert out.startswith(b"%PDF-")


def test_text_layer_is_searchable() -> None:
    pdf_bytes = _single_page_pdf()
    boxes = [
        WordBox(text="HELLO", x=100, y=100, width=80, height=30, confidence=95.0),
        WordBox(text="WORLD", x=200, y=100, width=80, height=30, confidence=95.0),
    ]
    out = add_text_layer(
        pdf_bytes,
        page_index=0,
        word_boxes=boxes,
        raster_width_px=2550,
        raster_height_px=3300,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        text_page = pdf[0].get_textpage()
        extracted = text_page.get_text_bounded()
        text_page.close()
    finally:
        pdf.close()
    assert "HELLO" in extracted
    assert "WORLD" in extracted


def test_empty_word_boxes_still_returns_valid_pdf() -> None:
    pdf_bytes = _single_page_pdf()
    out = add_text_layer(
        pdf_bytes,
        page_index=0,
        word_boxes=[],
        raster_width_px=2550,
        raster_height_px=3300,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        assert len(pdf) == 1
    finally:
        pdf.close()


# --- Native-text style extraction -------------------------------------------
#
# extract_native_word_boxes recovers the original glyph's font/size/colour/
# baseline so a downstream redactor can match it. These build a PDF with a known
# standard-14 font, size and fill colour, then assert the round-trip.

_PAGE_W_PT = 612.0
_PAGE_H_PT = 792.0


def _native_text_pdf(
    text: str,
    *,
    base_font: str = "Helvetica",
    size: int = 12,
    baseline_pt: float = 720.0,
    x_pt: float = 72.0,
    fill_rgb: str = "0 0 0",
) -> bytes:
    """A one-page PDF drawing ``text`` once with an exactly-known style.

    ``base_font`` is a standard-14 name (Helvetica/Times-Roman/Courier/...);
    ``fill_rgb`` is a PDF ``r g b`` triple in 0..1. The text baseline sits at
    ``baseline_pt`` from the page bottom (PDF's Y-up point space).
    """
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(_PAGE_W_PT, _PAGE_H_PT))
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type1,
            BaseFont=pikepdf.Name(f"/{base_font}"),
            Encoding=pikepdf.Name.WinAnsiEncoding,
        )
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    stream = f"BT /F1 {size} Tf {fill_rgb} rg {x_pt} {baseline_pt} Td ({text}) Tj ET"
    page.Contents = pdf.make_stream(stream.encode("ascii"))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _only_word(pdf_bytes: bytes, *, raster_w: int = 1224, raster_h: int = 1584) -> WordBox:
    """Extract and return the single word from a one-word native PDF (raster 2x)."""
    boxes = extract_native_word_boxes(
        pdf_bytes, page_index=0, raster_width_px=raster_w, raster_height_px=raster_h
    )
    assert len(boxes) == 1, f"expected one word, got {[b.text for b in boxes]}"
    return boxes[0]


def test_native_extraction_recovers_font_size_and_color() -> None:
    box = _only_word(_native_text_pdf("Marcus", base_font="Helvetica", size=12))
    assert box.text == "Marcus"
    assert box.font_name is not None
    assert "Helvetica" in box.font_name
    assert box.font_size_pt == pytest.approx(12.0, abs=0.5)
    assert box.color == (0, 0, 0)
    assert box.font_flags is not None


def test_native_extraction_recovers_nonblack_fill_color() -> None:
    # Pure red fill (1 0 0) must come back as (255, 0, 0) — proves the colour is
    # read from the content stream, not hard-coded to black.
    box = _only_word(_native_text_pdf("Redacted", fill_rgb="1 0 0"))
    assert box.color == (255, 0, 0)


def test_native_extraction_baseline_matches_drawn_baseline() -> None:
    # Use a DESCENDER-bearing word ("gypsy"): its glyph box bottom sits BELOW the
    # baseline, so a regression that returned the box bottom (instead of the text
    # matrix's true baseline) would be off by the descender depth and get caught.
    # Baseline drawn at 720pt from the bottom on a 792pt page; with a 2x raster
    # (raster_h/page_h = 2.0) that is (792-720)*2 = 144px from the top.
    box = _only_word(_native_text_pdf("gypsy", baseline_pt=720.0))
    assert box.baseline_y is not None
    assert box.baseline_y == pytest.approx((_PAGE_H_PT - 720.0) * (1584 / _PAGE_H_PT), abs=3.0)
    # The true baseline is ABOVE the box bottom (descenders extend below it),
    # which is exactly what distinguishes a matrix-derived baseline from box geometry.
    assert box.baseline_y < box.y + box.height


def test_native_extraction_recovers_base_font_name() -> None:
    # The standard-14 fonts (Helvetica/Times/Courier) ship no FontDescriptor, so
    # pdfium cannot derive the Serif/FixedPitch flag bits from them — it reports
    # only Nonsymbolic (32). Family classification therefore has to be NAME-first
    # (the consumer maps "Times"/"Courier" to a serif/mono substitute); the flag
    # bits are a bonus that only embedded, descriptor-carrying fonts provide.
    serif = _only_word(_native_text_pdf("Patient", base_font="Times-Roman"))
    assert serif.font_name is not None
    assert "Times" in serif.font_name
    mono = _only_word(_native_text_pdf("Patient", base_font="Courier"))
    assert mono.font_name is not None
    assert "Courier" in mono.font_name


def test_ocr_wordbox_leaves_style_none() -> None:
    # The OCR path constructs WordBox without style kwargs; a scan has no font
    # metadata, so every style field must default to None.
    box = WordBox(text="HELLO", x=1, y=2, width=3, height=4, confidence=95.0)
    assert box.font_name is None
    assert box.font_flags is None
    assert box.font_size_pt is None
    assert box.font_weight is None
    assert box.color is None
    assert box.baseline_y is None
