"""Tests for per-page MRC scoring."""

from __future__ import annotations

import io

import pikepdf
import pytest

from pdf_smasher.engine.page_classifier import score_pages_for_mrc


def _make_text_only_pdf() -> bytes:
    """A pure-text PDF: one Helvetica-Tj page, no image XObjects."""
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


def _make_image_only_pdf(image_bytes: bytes = b"\x00" * 50_000) -> bytes:
    """A pure-image PDF: one page with a large /XObject /Image."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image_stream = pdf.make_stream(
        image_bytes,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=100,
        Height=100,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Filter=pikepdf.Name.FlateDecode,
    )
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=image_stream),
    )
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def test_text_only_page_returns_false() -> None:
    flags = score_pages_for_mrc(_make_text_only_pdf())
    assert flags == [False]


def test_image_dominated_page_returns_true() -> None:
    flags = score_pages_for_mrc(_make_image_only_pdf())
    assert flags == [True]


def test_mixed_pdf_returns_per_page_decisions() -> None:
    text_pdf = _make_text_only_pdf()
    image_pdf = _make_image_only_pdf()
    pdf = pikepdf.open(io.BytesIO(text_pdf))
    img_pdf = pikepdf.open(io.BytesIO(image_pdf))
    pdf.pages.append(img_pdf.pages[0])
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    img_pdf.close()
    pdf.close()
    flags = score_pages_for_mrc(buf.getvalue())
    assert flags == [False, True]


def test_threshold_override_disables_gate() -> None:
    text_pdf = _make_text_only_pdf()
    flags = score_pages_for_mrc(text_pdf, min_image_byte_fraction=0.0)
    assert flags == [True]


def test_classifier_propagates_pikepdf_open_errors() -> None:
    """The per-page fallback applies to per-page analysis errors only;
    PDF-level open errors propagate so callers can route through
    ``_enforce_input_policy``."""
    with pytest.raises(pikepdf.PdfError):
        score_pages_for_mrc(b"not a pdf at all")
