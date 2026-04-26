"""Unit tests for native text-layer extraction (``extract_native_word_boxes``).

These exercise the pdfium textpage walker without depending on any
real-world PDF — synthetic pikepdf-built fixtures keep the tests fast
and deterministic.
"""

from __future__ import annotations

import io

import pikepdf

from pdf_smasher.engine.text_layer import extract_native_word_boxes


def _make_pdf_with_text() -> bytes:
    """Construct a 1-page PDF containing "Hello World" via a content stream
    that emits Tj operators with built-in Helvetica.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
                Encoding=pikepdf.Name.WinAnsiEncoding,
            ),
        )
    )
    page.Contents = pdf.make_stream(b"BT /F1 24 Tf 100 700 Td (Hello World) Tj ET\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def test_extracts_words_from_pdf_with_text_layer() -> None:
    boxes = extract_native_word_boxes(
        _make_pdf_with_text(),
        page_index=0,
        raster_width_px=1224,
        raster_height_px=1584,
    )
    texts = [b.text for b in boxes]
    assert "Hello" in texts
    assert "World" in texts
    for b in boxes:
        assert b.x >= 0
        assert b.y >= 0
        assert b.width > 0
        assert b.height > 0
        assert b.confidence == 100.0


def test_blank_page_returns_empty_list() -> None:
    boxes = extract_native_word_boxes(
        _make_blank_pdf(),
        page_index=0,
        raster_width_px=1224,
        raster_height_px=1584,
    )
    assert boxes == []


def test_coords_land_in_raster_pixel_space_top_left_origin() -> None:
    """Tj at PDF (100, 700) on a 612x792 page should land in the upper-left
    quadrant of the 2x-scaled raster: x near left, y near top (raster has
    top-left origin while PDF has bottom-left).
    """
    boxes = extract_native_word_boxes(
        _make_pdf_with_text(),
        page_index=0,
        raster_width_px=1224,
        raster_height_px=1584,
    )
    assert boxes
    hello = next((b for b in boxes if b.text == "Hello"), None)
    assert hello is not None
    assert hello.x < 1224 // 3
    assert hello.y < 1584 // 3
