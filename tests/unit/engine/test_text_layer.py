"""Tests for hankpdf.engine.text_layer — invisible OCR text.

We add a text layer to the composed PDF so users can select + search text
that was recognized by OCR. Rendering mode 3 (invisible) means the text is
present in the PDF but not painted to the screen — downstream search +
copy/paste work, visual fidelity is unchanged.
"""

from __future__ import annotations

import pypdfium2 as pdfium

from hankpdf.engine.ocr import WordBox
from hankpdf.engine.text_layer import add_text_layer


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
