"""Unit tests for compress() module-level helpers."""

from __future__ import annotations

import io

import pikepdf

from hankpdf import _extract_ground_truth_text


def _pdf_with_text_layer(text: str) -> bytes:
    """Minimal PDF with a native text layer containing `text`."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Resources = resources
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode()
    page.Contents = pdf.make_stream(content)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_extract_ground_truth_prefers_native_text_layer() -> None:
    """_extract_ground_truth_text returns the native text when present,
    ignoring the fallback OCR text (which might be wrong for small fonts)."""
    pdf_bytes = _pdf_with_text_layer("Invoice 1234")
    result = _extract_ground_truth_text(pdf_bytes, 0, fallback_ocr_text="WRONG OCR")
    assert "1234" in result, f"native text not extracted; got: {result!r}"


def test_extract_ground_truth_falls_back_when_no_text_layer() -> None:
    """When the PDF has no native text layer, fallback_ocr_text is returned."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    pdf_bytes = buf.getvalue()
    result = _extract_ground_truth_text(pdf_bytes, 0, fallback_ocr_text="OCR TEXT")
    assert result == "OCR TEXT", f"should fall back to OCR; got: {result!r}"
