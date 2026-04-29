"""Unit tests for native text-layer extraction (``extract_native_word_boxes``).

These exercise the pdfium textpage walker without depending on any
real-world PDF — synthetic pikepdf-built fixtures keep the tests fast
and deterministic.
"""

from __future__ import annotations

import io

import pikepdf

from hankpdf.engine.ocr import WordBox
from hankpdf.engine.text_layer import (
    extract_native_word_boxes,
    is_native_text_decent,
)


def _word(text: str) -> WordBox:
    """Synthetic WordBox; coordinates don't matter for the decency heuristic."""
    return WordBox(text=text, x=0, y=0, width=1, height=1, confidence=100.0)


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


def test_is_native_text_decent_accepts_real_words() -> None:
    boxes = [
        _word(w) for w in ["Scaling", "Anesthesia", "Billing", "and", "Compliance", "Documentation"]
    ]
    assert is_native_text_decent(boxes)


def test_is_native_text_decent_rejects_single_char_floods() -> None:
    # The "Scal i ng" pattern: each glyph as its own word.
    boxes = [_word(c) for c in ["Scal", "i", "ng", "Anest", "hesi", "a", "Bi", "l", "l", "i", "ng"]]
    assert not is_native_text_decent(boxes)


def test_is_native_text_decent_rejects_symbol_noise() -> None:
    # Need >30 chars total to escape the sparse-page exception, then the
    # alpha-or-space gate fires.
    boxes = [_word("???###|||$$$%%%@@@") for _ in range(4)]
    assert not is_native_text_decent(boxes)


def test_is_native_text_decent_rejects_long_garbage_runs() -> None:
    # Gibberish OCR signature: ultra-long pseudo-words.
    boxes = [_word("a" * 30) for _ in range(8)]
    assert not is_native_text_decent(boxes)


def test_is_native_text_decent_accepts_sparse_pages() -> None:
    # Cover pages, dividers — light on text, but what's there is real.
    boxes = [_word("Cover")]
    assert is_native_text_decent(boxes)


def test_is_native_text_decent_rejects_empty_list() -> None:
    assert not is_native_text_decent([])


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
