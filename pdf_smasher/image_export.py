"""Render PDF pages to JPEG or PNG images.

A separate output mode from the MRC compress pipeline — skips mask /
classify / compose / verify entirely and just rasterizes each requested
page, then encodes via Pillow. Supports both JPEG (lossy, ``jpeg_quality``
0-100) and PNG (lossless, ``png_compress_level`` 0-9).

All codecs used here are permissively licensed (see THIRD_PARTY_LICENSES.md):
JPEG via libjpeg-turbo (IJG/BSD), PNG via libpng + zlib.
"""

from __future__ import annotations

import io
from typing import Literal

from pdf_smasher.engine.rasterize import rasterize_page

ImageFormat = Literal["jpeg", "png"]

_JPEG_SUBSAMPLING_444 = 0  # match compose.py default (no chroma subsampling)

_SUPPORTED_FORMATS: tuple[str, ...] = ("jpeg", "png")

# PNG zlib level 9 activates Pillow's extra-optimize pass (huffman tuning etc.)
# at the cost of CPU. 6 is the default; any other level skips the pass.
_PNG_OPTIMIZE_LEVEL = 9


def render_pages_as_images(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int = 150,
    jpeg_quality: int = 75,
    jpeg_subsampling: int = _JPEG_SUBSAMPLING_444,
    png_compress_level: int = 6,
) -> list[bytes]:
    """Rasterize the requested pages and return one encoded image per page.

    Parameters
    ----------
    pdf_bytes:
        The source PDF.
    page_indices:
        Zero-indexed page numbers to render. Order is preserved — the
        returned list's i-th element corresponds to ``page_indices[i]``.
    image_format:
        ``"jpeg"`` (lossy) or ``"png"`` (lossless).
    dpi:
        Rasterization density. 150 is a reasonable screen default; 300
        matches print / archival.
    jpeg_quality:
        0-100 for JPEG. Ignored for PNG.
    jpeg_subsampling:
        Chroma subsampling index (0=4:4:4, 1=4:2:2, 2=4:2:0). Ignored for PNG.
    png_compress_level:
        0-9 for PNG zlib deflate level. 0 = no compression (fastest, largest),
        9 = max compression (slowest, smallest). 6 is the Pillow default.
        Ignored for JPEG.

    Returns
    -------
    list[bytes]
        One encoded image per index, in the order given.

    Raises
    ------
    ValueError
        If ``image_format`` is not recognized.
    IndexError / ValueError
        If any ``page_indices`` entry is out of range for the source PDF.
    """
    if image_format not in _SUPPORTED_FORMATS:
        msg = f"image_format must be one of {_SUPPORTED_FORMATS}; got {image_format!r}"
        raise ValueError(msg)
    if not page_indices:
        return []

    out: list[bytes] = []
    for page_index in page_indices:
        raster = rasterize_page(pdf_bytes, page_index=page_index, dpi=dpi)
        buf = io.BytesIO()
        if image_format == "jpeg":
            # optimize=True uses two-pass Huffman; runs out of buffer
            # space on large high-quality images ("Suspension not allowed
            # here"). Skip it — the 2-5% size saving isn't worth the
            # reliability hit for users who want full-DPI page exports.
            raster.convert("RGB").save(
                buf,
                format="JPEG",
                quality=jpeg_quality,
                subsampling=jpeg_subsampling,
            )
        else:  # png
            raster.convert("RGB").save(
                buf,
                format="PNG",
                compress_level=png_compress_level,
                optimize=(png_compress_level == _PNG_OPTIMIZE_LEVEL),
            )
        out.append(buf.getvalue())
    return out
