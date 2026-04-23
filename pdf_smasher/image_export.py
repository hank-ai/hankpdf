"""Render PDF pages to JPEG, PNG, or WebP images.

A separate output mode from the MRC compress pipeline - skips mask /
classify / compose / verify entirely and just rasterizes each requested
page, then encodes via Pillow.

Formats:
  - ``jpeg`` (lossy, ``jpeg_quality`` 0-100) via libjpeg-turbo
  - ``png``  (lossless, ``png_compress_level`` 0-9) via libpng + zlib
  - ``webp`` (lossy or lossless, ``webp_quality`` 0-100) via libwebp

All three codecs are permissively licensed (see THIRD_PARTY_LICENSES.md):
libjpeg-turbo (IJG/BSD), libpng + zlib (BSD/zlib), libwebp (BSD-3-Clause).
Pillow itself is MIT-CMU.
"""

from __future__ import annotations

import io
from typing import Literal

from pdf_smasher.engine.rasterize import rasterize_page

ImageFormat = Literal["jpeg", "png", "webp"]

_JPEG_SUBSAMPLING_444 = 0  # match compose.py default (no chroma subsampling)

_SUPPORTED_FORMATS: tuple[str, ...] = ("jpeg", "png", "webp")

# PNG zlib level 9 activates Pillow's extra-optimize pass (huffman tuning etc.)
# at the cost of CPU. 6 is the default; any other level skips the pass.
_PNG_OPTIMIZE_LEVEL = 9

# libwebp method: 0=fastest/largest, 6=slowest/smallest. 4 is Pillow's default
# and a reasonable speed/size balance.
_WEBP_METHOD_DEFAULT = 4


def render_pages_as_images(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int = 150,
    jpeg_quality: int = 75,
    jpeg_subsampling: int = _JPEG_SUBSAMPLING_444,
    png_compress_level: int = 6,
    webp_quality: int = 80,
    webp_lossless: bool = False,
    webp_method: int = _WEBP_METHOD_DEFAULT,
) -> list[bytes]:
    """Rasterize the requested pages and return one encoded image per page.

    Parameters
    ----------
    pdf_bytes:
        The source PDF.
    page_indices:
        Zero-indexed page numbers to render. Order is preserved - the
        returned list's i-th element corresponds to ``page_indices[i]``.
    image_format:
        ``"jpeg"`` (lossy), ``"png"`` (lossless), or ``"webp"`` (lossy
        by default; opt into lossless via ``webp_lossless=True``).
    dpi:
        Rasterization density. 150 is a reasonable screen default; 300
        matches print / archival.
    jpeg_quality:
        0-100 for JPEG. Ignored for PNG/WebP.
    jpeg_subsampling:
        Chroma subsampling index (0=4:4:4, 1=4:2:2, 2=4:2:0). JPEG only.
    png_compress_level:
        0-9 for PNG zlib deflate level. 0 = no compression (fastest,
        largest), 9 = max compression (slowest, smallest). 6 is the
        Pillow default. Ignored for JPEG/WebP.
    webp_quality:
        0-100 for WebP lossy. For WebP lossless (``webp_lossless=True``)
        this controls encoder effort, not fidelity. Ignored for JPEG/PNG.
    webp_lossless:
        When True, WebP writes the pixel data losslessly (bigger file but
        bit-exact decode). WebP only.
    webp_method:
        0-6 for the WebP encoder speed/size tradeoff. 0 = fastest, 6 =
        smallest. WebP only.

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
        rgb = raster.convert("RGB")
        if image_format == "jpeg":
            # optimize=True uses two-pass Huffman; runs out of buffer
            # space on large high-quality images ("Suspension not allowed
            # here"). Skip it - the 2-5% size saving isn't worth the
            # reliability hit for users who want full-DPI page exports.
            rgb.save(
                buf,
                format="JPEG",
                quality=jpeg_quality,
                subsampling=jpeg_subsampling,
            )
        elif image_format == "png":
            rgb.save(
                buf,
                format="PNG",
                compress_level=png_compress_level,
                optimize=(png_compress_level == _PNG_OPTIMIZE_LEVEL),
            )
        else:  # webp
            rgb.save(
                buf,
                format="WEBP",
                quality=webp_quality,
                lossless=webp_lossless,
                method=webp_method,
            )
        out.append(buf.getvalue())
    return out
