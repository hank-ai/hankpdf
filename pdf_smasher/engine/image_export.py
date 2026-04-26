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
from collections.abc import Callable, Iterator
from typing import Literal

import PIL.Image
import pypdfium2 as pdfium

from pdf_smasher._limits import (
    MAX_BOMB_PIXELS as _MAX_BOMB_PIXELS,  # noqa: F401  — re-exported for tests/unit/test_pillow_hardening.py
)
from pdf_smasher._pillow_hardening import ensure_capped
from pdf_smasher.engine._render_safety import check_render_size
from pdf_smasher.engine.rasterize import rasterize_page
from pdf_smasher.exceptions import DecompressionBombError

ensure_capped()

ImageFormat = Literal["jpeg", "png", "webp"]

_JPEG_SUBSAMPLING_444 = 0  # match compose.py default (no chroma subsampling)

_SUPPORTED_FORMATS: tuple[str, ...] = ("jpeg", "png", "webp")

# PNG zlib level 9 activates Pillow's extra-optimize pass (huffman tuning etc.)
# at the cost of CPU. 6 is the default; any other level skips the pass.
_PNG_OPTIMIZE_LEVEL = 9

# libwebp method: 0=fastest/largest, 6=slowest/smallest. 4 is Pillow's default
# and a reasonable speed/size balance.
_WEBP_METHOD_DEFAULT = 4

# Decompression-bomb cap: refuse rasters larger than ~2 GB RGB. At 3 bytes
# per pixel this is the maximum unsigned 32-bit allocation we allow.
# SINGLE source of truth is pdf_smasher._limits.MAX_BOMB_PIXELS, shared
# with PIL.Image.MAX_IMAGE_PIXELS (installed by _pillow_hardening).
# _MAX_BOMB_PIXELS is a local alias for backward-compat with existing
# call sites inside this module.

# Library-level DPI cap. The CLI enforces this via _positive_dpi argparse
# type, but library callers (programmatic use) were previously unbounded —
# dpi=99999 on a letter page is a 600k-pixel-wide raster. Keep in sync with
# cli.main._MAX_IMAGE_DPI (which is imported from here).
_MAX_IMAGE_DPI_LIB = 1200


def iter_pages_as_images(
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
    progress_callback: Callable[[str, int, int], None] | None = None,
    _force_rasterize_error_for_test: bool = False,
    _simulate_huge_page_for_test: bool = False,
    password: str | None = None,
) -> Iterator[bytes]:
    """Streaming counterpart to :func:`render_pages_as_images`.

    Yields one encoded image per requested page, never buffers more
    than one in memory. Callers can write-as-they-go to avoid OOM on
    huge batches (300 DPI x 400 pages ~= 8 GB if buffered).

    Parameters are identical to :func:`render_pages_as_images`.

    Raises
    ------
    ValueError
        If ``image_format`` is not recognized or ``dpi`` exceeds the
        library cap ``_MAX_IMAGE_DPI_LIB``. Raised at call time (before
        the generator is returned), so callers that pass bad values see
        the error immediately, not only once they iterate.
    DecompressionBombError
        If a page's pixel budget would exceed the bomb cap. Raised
        BEFORE pdfium allocates, so huge pages never touch RAM.
    RuntimeError
        Wraps any rasterize/encode failure with
        ``"page {N+1}/{total}"`` context. The underlying exception is
        chained via ``__cause__``.
    """
    if image_format not in _SUPPORTED_FORMATS:
        msg = f"image_format must be one of {_SUPPORTED_FORMATS}; got {image_format!r}"
        raise ValueError(msg)
    if dpi < 1:
        msg = f"dpi must be >= 1 (got {dpi})"
        raise ValueError(msg)
    if dpi > _MAX_IMAGE_DPI_LIB:
        msg = (
            f"dpi {dpi} exceeds the library cap of {_MAX_IMAGE_DPI_LIB}. "
            "Higher values can exceed addressable memory on realistic "
            "page sizes."
        )
        raise ValueError(msg)

    return _iter_pages_impl(
        pdf_bytes,
        page_indices,
        image_format=image_format,
        dpi=dpi,
        jpeg_quality=jpeg_quality,
        jpeg_subsampling=jpeg_subsampling,
        png_compress_level=png_compress_level,
        webp_quality=webp_quality,
        webp_lossless=webp_lossless,
        webp_method=webp_method,
        progress_callback=progress_callback,
        _force_rasterize_error_for_test=_force_rasterize_error_for_test,
        _simulate_huge_page_for_test=_simulate_huge_page_for_test,
        password=password,
    )


def _reraise_per_page_error(
    exc: BaseException,
    page_index: int,
    total: int,
) -> None:
    """Re-raise a per-page exception with the appropriate typing.

    Consolidates the per-page error translation so ``_iter_pages_impl``'s
    ``except`` stays a single branch (keeps the PLR0912 branch count under
    ruff's cap). Never returns normally.

    - ``DecompressionBombError`` (ours): propagate unchanged so the CLI can
      route it to ``EXIT_DECOMPRESSION_BOMB=16``.
    - ``PIL.Image.DecompressionBombError`` (Pillow's class, NOT a subclass
      of ours): translate to our typed class so the CLI's
      ``except DecompressionBombError`` routes uniformly.
    - Anything else: wrap in ``RuntimeError`` with ``"page N/total"``
      context. Original chained via ``__cause__``.
    """
    if isinstance(exc, DecompressionBombError):
        raise exc
    if isinstance(exc, PIL.Image.DecompressionBombError):
        raise DecompressionBombError(str(exc)) from exc
    wrapped = f"image export failed on page {page_index + 1}/{total}: {exc}"
    raise RuntimeError(wrapped) from exc


def _page_size_points(
    pdf_bytes: bytes, page_index: int, *, password: str | None = None
) -> tuple[float, float]:
    """Open the PDF briefly and return (width_pt, height_pt) for the given
    page WITHOUT rasterizing. Used for the pre-allocation pixel-budget check.
    """
    doc = pdfium.PdfDocument(pdf_bytes, password=password)
    try:
        page = doc[page_index]
        try:
            size = page.get_size()  # (width_pt, height_pt)
            return float(size[0]), float(size[1])
        finally:
            page.close()
    finally:
        doc.close()


def _iter_pages_impl(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int,
    jpeg_quality: int,
    jpeg_subsampling: int,
    png_compress_level: int,
    webp_quality: int,
    webp_lossless: bool,
    webp_method: int,
    progress_callback: Callable[[str, int, int], None] | None,
    _force_rasterize_error_for_test: bool,
    _simulate_huge_page_for_test: bool,
    password: str | None = None,
) -> Iterator[bytes]:
    """Real generator body for :func:`iter_pages_as_images`.

    Kept private so the public entry point can validate ``image_format``
    synchronously (raising ``ValueError`` at call time rather than at
    first ``next()``).
    """
    total = len(page_indices)
    for pos, page_index in enumerate(page_indices, start=1):
        if progress_callback is not None:
            progress_callback("page_start", pos, total)
        try:
            # Pre-allocation pixel-budget check. Do this BEFORE
            # rasterize_page is called so pdfium never allocates a
            # bomb-sized bitmap. We extract just the page size (cheap)
            # and compute the target raster dimensions from DPI.
            if _simulate_huge_page_for_test:
                width_pt, height_pt = 100_000.0, 100_000.0
            else:
                width_pt, height_pt = _page_size_points(pdf_bytes, page_index, password=password)
            check_render_size(width_pt=width_pt, height_pt=height_pt, dpi=dpi)
            if _force_rasterize_error_for_test:
                _forced = "forced test error"
                raise RuntimeError(_forced)
            raster = rasterize_page(pdf_bytes, page_index=page_index, dpi=dpi, password=password)
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
            encoded = buf.getvalue()
        except Exception as exc:  # noqa: BLE001 — helper re-raises every path (never returns)
            _reraise_per_page_error(exc, page_index, total)
        yield encoded
        if progress_callback is not None:
            progress_callback("page_done", pos, total)


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
    progress_callback: Callable[[str, int, int], None] | None = None,
    _force_rasterize_error_for_test: bool = False,
    _simulate_huge_page_for_test: bool = False,
) -> list[bytes]:
    """Rasterize the requested pages and return one encoded image per page.

    Eager counterpart to :func:`iter_pages_as_images`. Kept for backward
    compatibility; prefer ``iter_pages_as_images`` for large batches
    where holding every encoded blob in memory would risk OOM.

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

    progress_callback receives (phase, current, total) triples:
        - ('page_start', i, total) at the top of each iteration.
        - ('page_done',  i, total) after each page is encoded.

    Exceptions raised during rasterize or encode are wrapped to include
    "page {i + 1}/{total}" in the message so logs tell you which page
    failed during which stage.
    """
    return list(
        iter_pages_as_images(
            pdf_bytes,
            page_indices,
            image_format=image_format,
            dpi=dpi,
            jpeg_quality=jpeg_quality,
            jpeg_subsampling=jpeg_subsampling,
            png_compress_level=png_compress_level,
            webp_quality=webp_quality,
            webp_lossless=webp_lossless,
            webp_method=webp_method,
            progress_callback=progress_callback,
            _force_rasterize_error_for_test=_force_rasterize_error_for_test,
            _simulate_huge_page_for_test=_simulate_huge_page_for_test,
        )
    )
