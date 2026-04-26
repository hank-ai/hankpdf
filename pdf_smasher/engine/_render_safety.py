"""Shared pre-allocation render-size cap.

Both compress (rasterize.py) and image-export (image_export.py) paths
must check that a page's pixel count fits before asking pdfium to
allocate the bitmap. Without this check on the compress path, a PDF
with an oversized MediaBox triggers a multi-GB allocation inside
pdfium before any of our own guards fire.

Canonical home of the cap is :data:`pdf_smasher._limits.MAX_BOMB_PIXELS` —
this module imports it and exposes :func:`check_render_size` plus an opt-in
``max_pixels`` override for callers that knowingly want a higher ceiling
(e.g., a future ``render-page`` CLI dealing with engineering drawings).
"""

from __future__ import annotations

from pdf_smasher._limits import MAX_BOMB_PIXELS
from pdf_smasher.exceptions import DecompressionBombError

_POINTS_PER_INCH: float = 72.0


def check_render_size(
    width_pt: float,
    height_pt: float,
    dpi: float,
    *,
    max_pixels: int = MAX_BOMB_PIXELS,
) -> None:
    """Refuse if rasterizing the page at ``dpi`` would exceed ``max_pixels``.

    :class:`pdf_smasher.exceptions.DecompressionBombError` is raised before
    any allocation happens. The CLI maps the exception to
    ``EXIT_DECOMPRESSION_BOMB=16``.

    Pass ``max_pixels`` higher than the default only when the caller has
    bounded the allocation by some other means.
    """
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError(
            f"invalid page size: width_pt={width_pt!r}, height_pt={height_pt!r}; "
            "non-positive values often indicate a locked/encrypted pdfium handle "
            "fell back to a stub document"
        )
    target_w = round(width_pt * dpi / _POINTS_PER_INCH)
    target_h = round(height_pt * dpi / _POINTS_PER_INCH)
    if target_w * target_h > max_pixels:
        raise DecompressionBombError(
            f"page would render to {target_w}x{target_h} pixels "
            f"({target_w * target_h:,} px), exceeding cap of {max_pixels:,}"
        )
