"""Rasterize a PDF page into a PIL image via pdfium.

Held under a process-global lock by the caller in any threaded context —
pdfium is not thread-safe (see docs/ARCHITECTURE.md §3 and §8.1).
"""

from __future__ import annotations

import pypdfium2 as pdfium
from PIL import Image

from pdf_smasher.engine._render_safety import check_render_size

_POINTS_PER_INCH = 72.0


def rasterize_page(
    pdf_bytes: bytes,
    *,
    page_index: int,
    dpi: int,
    password: str | None = None,
) -> Image.Image:
    """Render a single page of ``pdf_bytes`` at ``dpi`` into a PIL RGB image.

    Parameters
    ----------
    pdf_bytes:
        The full input PDF as a ``bytes`` object.
    page_index:
        0-based page index. ``IndexError`` if out of range.
    dpi:
        Target rasterization DPI. Output dimensions are
        ``(width_points/72, height_points/72) * dpi``.

    Returns
    -------
    PIL.Image.Image
        RGB-mode image. 8 bits per channel — pdfium's public render API
        maxes out at 8-bit depth.
    """
    pdf = pdfium.PdfDocument(pdf_bytes, password=password)
    try:
        if page_index < 0 or page_index >= len(pdf):
            msg = f"page_index {page_index} out of range for {len(pdf)}-page document"
            raise IndexError(msg)
        page = pdf[page_index]
        width_pt, height_pt = page.get_size()
        check_render_size(width_pt=width_pt, height_pt=height_pt, dpi=dpi)
        target_w = round(width_pt * dpi / _POINTS_PER_INCH)
        target_h = round(height_pt * dpi / _POINTS_PER_INCH)
        scale = dpi / _POINTS_PER_INCH
        bitmap = page.render(scale=scale)
        pil: Image.Image = bitmap.to_pil().convert("RGB")
        if pil.size != (target_w, target_h):
            pil = pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
        return pil
    finally:
        pdf.close()
