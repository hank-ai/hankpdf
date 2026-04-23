"""Tests for pdf_smasher.engine.rasterize.

First spike task (Phase 1 T1.1): rasterize a PDF page via pdfium at a given
DPI into a PIL image we can hand to OpenCV + Tesseract downstream.
"""

from __future__ import annotations

import pypdfium2 as pdfium
import pytest
from PIL import Image

from pdf_smasher.engine.rasterize import rasterize_page


def _make_minimal_pdf(width_points: int = 612, height_points: int = 792) -> bytes:
    """Build a 1-page empty PDF of the given page size. Default is US Letter."""
    pdf = pdfium.PdfDocument.new()
    pdf.new_page(float(width_points), float(height_points))
    import io

    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_rasterize_page_returns_pil_image() -> None:
    """rasterize_page should return a PIL Image, not a pdfium bitmap."""
    pdf_bytes = _make_minimal_pdf()
    result = rasterize_page(pdf_bytes, page_index=0, dpi=150)
    assert isinstance(result, Image.Image)


def test_rasterize_page_dimensions_match_dpi_and_page_size() -> None:
    """A US Letter page (8.5 x 11 in) at 300 DPI rasterizes to 2550 x 3300 px."""
    pdf_bytes = _make_minimal_pdf(width_points=612, height_points=792)
    result = rasterize_page(pdf_bytes, page_index=0, dpi=300)
    # 612 points = 8.5 in; 792 points = 11 in; points are 1/72 in.
    # width_px = width_in * dpi = (612/72) * 300 = 2550
    # height_px = (792/72) * 300 = 3300
    assert result.size == (2550, 3300)


def test_rasterize_page_dimensions_scale_with_dpi() -> None:
    """Halving the DPI halves each dimension."""
    pdf_bytes = _make_minimal_pdf(width_points=612, height_points=792)
    low = rasterize_page(pdf_bytes, page_index=0, dpi=150)
    high = rasterize_page(pdf_bytes, page_index=0, dpi=300)
    assert high.size[0] == 2 * low.size[0]
    assert high.size[1] == 2 * low.size[1]


def test_rasterize_page_mode_is_rgb() -> None:
    """Default output mode is RGB (no alpha) — downstream OpenCV path expects 3 channels."""
    pdf_bytes = _make_minimal_pdf()
    result = rasterize_page(pdf_bytes, page_index=0, dpi=72)
    assert result.mode == "RGB"


def test_rasterize_page_invalid_page_index_raises() -> None:
    """Asking for a page beyond the document should raise IndexError, not segfault."""
    pdf_bytes = _make_minimal_pdf()  # 1 page
    with pytest.raises(IndexError):
        rasterize_page(pdf_bytes, page_index=5, dpi=72)


def test_rasterize_page_non_square_page() -> None:
    """Portrait vs landscape — dimensions reflect actual /MediaBox, not assumed square."""
    pdf_bytes = _make_minimal_pdf(width_points=792, height_points=612)  # landscape
    result = rasterize_page(pdf_bytes, page_index=0, dpi=72)
    assert result.size == (792, 612)
