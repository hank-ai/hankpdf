"""Tests for pdf_smasher.image_export.render_pages_as_images."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image

from pdf_smasher.engine.image_export import iter_pages_as_images, render_pages_as_images


def _make_pdf(n_pages: int) -> bytes:
    """N-page PDF. Each page has a unique height (792 + i pt) so per-page
    ordering can be verified via the rendered image's height.
    """
    pdf = pikepdf.new()
    for i in range(n_pages):
        pdf.add_blank_page(page_size=(612.0, 792.0 + i))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _make_rich_pdf() -> bytes:
    """Single-page PDF with a high-frequency gradient image stamped in, so
    JPEG quality / PNG compression level have something meaningful to act on.
    """
    import numpy as np

    arr = np.random.default_rng(seed=1).integers(0, 256, size=(1000, 800, 3)).astype(np.uint8)
    src = Image.fromarray(arr)
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612.0, 792.0))
    page = pdf.pages[0]
    buf = io.BytesIO()
    src.save(buf, format="JPEG", quality=95, subsampling=0)
    xobj = pdf.make_stream(
        buf.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=800,
        Height=1000,
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def test_jpeg_produces_valid_jpeg_bytes() -> None:
    pdf_bytes = _make_pdf(2)
    blobs = render_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1],
        image_format="jpeg",
        dpi=72,
    )
    assert len(blobs) == 2
    for blob in blobs:
        assert blob[:2] == b"\xff\xd8"  # JPEG SOI marker
        Image.open(io.BytesIO(blob)).verify()  # PIL verify round-trip


def test_png_produces_valid_png_bytes() -> None:
    pdf_bytes = _make_pdf(2)
    blobs = render_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1],
        image_format="png",
        dpi=72,
    )
    assert len(blobs) == 2
    for blob in blobs:
        assert blob[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
        Image.open(io.BytesIO(blob)).verify()


def test_respects_page_indices_order() -> None:
    pdf_bytes = _make_pdf(3)
    # Request pages 2 and 0 — page 2 is taller, should come out first.
    blobs = render_pages_as_images(
        pdf_bytes,
        page_indices=[2, 0],
        image_format="jpeg",
        dpi=72,
    )
    h0 = Image.open(io.BytesIO(blobs[0])).height
    h1 = Image.open(io.BytesIO(blobs[1])).height
    assert h0 > h1, f"expected page-2 height > page-0 height, got {h0} vs {h1}"


def test_dpi_controls_image_dimensions() -> None:
    pdf_bytes = _make_pdf(1)
    lo = render_pages_as_images(pdf_bytes, page_indices=[0], image_format="jpeg", dpi=72)[0]
    hi = render_pages_as_images(pdf_bytes, page_indices=[0], image_format="jpeg", dpi=216)[0]
    lo_img = Image.open(io.BytesIO(lo))
    hi_img = Image.open(io.BytesIO(hi))
    # 216 DPI is 3x 72 DPI; pixel dims scale accordingly.
    assert hi_img.width == pytest.approx(lo_img.width * 3, abs=2)
    assert hi_img.height == pytest.approx(lo_img.height * 3, abs=2)


def test_jpeg_quality_controls_file_size() -> None:
    pdf_bytes = _make_rich_pdf()
    hi_q = render_pages_as_images(
        pdf_bytes,
        page_indices=[0],
        image_format="jpeg",
        dpi=150,
        jpeg_quality=90,
    )[0]
    lo_q = render_pages_as_images(
        pdf_bytes,
        page_indices=[0],
        image_format="jpeg",
        dpi=150,
        jpeg_quality=30,
    )[0]
    assert len(lo_q) < len(hi_q), (
        f"quality=30 ({len(lo_q):,}) should be smaller than quality=90 ({len(hi_q):,})"
    )


def test_png_compress_level_controls_file_size() -> None:
    """PNG is lossless; higher compress_level → smaller file, same pixels."""
    pdf_bytes = _make_rich_pdf()
    weak = render_pages_as_images(
        pdf_bytes,
        page_indices=[0],
        image_format="png",
        dpi=100,
        png_compress_level=1,
    )[0]
    strong = render_pages_as_images(
        pdf_bytes,
        page_indices=[0],
        image_format="png",
        dpi=100,
        png_compress_level=9,
    )[0]
    assert len(strong) <= len(weak), (
        f"compress_level=9 ({len(strong):,}) should be <= level=1 ({len(weak):,})"
    )
    # Both decode to the same pixels.
    a = Image.open(io.BytesIO(weak)).convert("RGB")
    b = Image.open(io.BytesIO(strong)).convert("RGB")
    assert a.tobytes() == b.tobytes()


def test_rejects_out_of_range_page_index() -> None:
    pdf_bytes = _make_pdf(2)
    # Per-page errors are now wrapped in RuntimeError with "page N/total"
    # context (Task 7); the underlying IndexError/ValueError is chained
    # via __cause__.
    with pytest.raises(RuntimeError, match="page 6"):
        render_pages_as_images(
            pdf_bytes,
            page_indices=[5],
            image_format="jpeg",
            dpi=72,
        )


def test_empty_indices_returns_empty_list() -> None:
    pdf_bytes = _make_pdf(1)
    imgs = render_pages_as_images(pdf_bytes, page_indices=[], image_format="jpeg", dpi=72)
    assert imgs == []


def test_rejects_unknown_format() -> None:
    pdf_bytes = _make_pdf(1)
    with pytest.raises(ValueError, match="image_format"):
        render_pages_as_images(
            pdf_bytes,
            page_indices=[0],
            image_format="tiff",  # type: ignore[arg-type]
            dpi=72,
        )


# ---------- WebP ----------


def test_webp_produces_valid_webp_bytes() -> None:
    pdf_bytes = _make_pdf(2)
    blobs = render_pages_as_images(
        pdf_bytes, page_indices=[0, 1], image_format="webp", dpi=72,
    )
    assert len(blobs) == 2
    for blob in blobs:
        # WebP files start with "RIFF" + size + "WEBP"
        assert blob[:4] == b"RIFF"
        assert blob[8:12] == b"WEBP"
        Image.open(io.BytesIO(blob)).verify()


def test_webp_quality_controls_file_size() -> None:
    """Lossy WebP: lower quality → smaller file."""
    pdf_bytes = _make_rich_pdf()
    hi_q = render_pages_as_images(
        pdf_bytes, page_indices=[0], image_format="webp", dpi=150, webp_quality=90,
    )[0]
    lo_q = render_pages_as_images(
        pdf_bytes, page_indices=[0], image_format="webp", dpi=150, webp_quality=30,
    )[0]
    assert len(lo_q) < len(hi_q), (
        f"webp quality=30 ({len(lo_q):,}) should be smaller than quality=90 "
        f"({len(hi_q):,})"
    )


def test_webp_lossless_preserves_pixels() -> None:
    """Lossless WebP: decoded pixels must exactly equal the source raster."""
    pdf_bytes = _make_pdf(1)
    blob = render_pages_as_images(
        pdf_bytes,
        page_indices=[0],
        image_format="webp",
        dpi=100,
        webp_lossless=True,
    )[0]
    # Source: render directly at same DPI.
    from pdf_smasher.engine.rasterize import rasterize_page

    src = rasterize_page(pdf_bytes, page_index=0, dpi=100).convert("RGB")
    decoded = Image.open(io.BytesIO(blob)).convert("RGB")
    assert src.size == decoded.size
    assert src.tobytes() == decoded.tobytes()


def test_webp_is_smaller_than_jpeg_at_matching_settings() -> None:
    """WebP typically beats JPEG by 25-35% at matching perceptual quality.
    Assert only the weaker claim that WebP <= JPEG at quality=75 on a
    natural-looking image (gradient + noise). Skip if codec misbehaves on
    a given platform (this is not a license test, just a sanity check).
    """
    pdf_bytes = _make_rich_pdf()
    jpeg = render_pages_as_images(
        pdf_bytes, page_indices=[0], image_format="jpeg", dpi=150, jpeg_quality=75,
    )[0]
    webp = render_pages_as_images(
        pdf_bytes, page_indices=[0], image_format="webp", dpi=150, webp_quality=75,
    )[0]
    assert len(webp) <= len(jpeg), (
        f"webp ({len(webp):,}) should be <= jpeg ({len(jpeg):,}) at q=75"
    )


def test_render_pages_emits_progress_events() -> None:
    """render_pages_as_images accepts an optional progress_callback and
    fires one event per completed page."""
    pdf_bytes = _make_pdf(3)
    events: list[tuple[int, int]] = []

    def _cb(phase: str, current: int, total: int) -> None:
        if phase == "page_done":
            events.append((current, total))

    render_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1, 2],
        image_format="jpeg",
        dpi=72,
        progress_callback=_cb,
    )
    # 3 page_done events, 1-indexed current, total=3.
    assert events == [(1, 3), (2, 3), (3, 3)], f"got {events}"


def test_render_pages_per_page_error_context() -> None:
    """When rasterize_page fails on page N, the raised exception must
    contain 'page {N+1}' so logs tell the user which page."""
    pdf_bytes = _make_pdf(5)
    with pytest.raises(Exception, match="page 3"):
        render_pages_as_images(
            pdf_bytes,
            page_indices=[2],  # 0-indexed -> displayed as 1-indexed page 3
            image_format="jpeg",
            dpi=72,
            _force_rasterize_error_for_test=True,  # new test hook
        )


def test_iter_pages_yields_bytes_lazily() -> None:
    """iter_pages_as_images must be a generator, yielding one encoded
    image per iteration without materializing the whole list."""
    import types

    pdf_bytes = _make_pdf(3)
    it = iter_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1, 2],
        image_format="jpeg",
        dpi=72,
    )
    assert isinstance(it, types.GeneratorType)
    first = next(it)
    assert first[:2] == b"\xff\xd8"
    rest = list(it)
    assert len(rest) == 2


def test_pre_allocation_pixel_budget_check() -> None:
    """Decompression bomb guard must refuse BEFORE pdfium allocates
    the raster. The synthetic hook bypasses the rasterize call entirely
    so the only way the error can be raised is the pre-allocation check.
    """
    pdf_bytes = _make_pdf(1)
    with pytest.raises(Exception, match=r"bomb|cap|exceed"):
        list(
            iter_pages_as_images(
                pdf_bytes,
                page_indices=[0],
                image_format="jpeg",
                dpi=1200,  # within CLI cap
                _simulate_huge_page_for_test=True,
            )
        )


def test_pillow_decompression_bomb_translated_to_our_class() -> None:
    """Pillow raises PIL.Image.DecompressionBombError; our code must catch
    it and re-raise as pdf_smasher.DecompressionBombError so the CLI can
    route it to EXIT_DECOMPRESSION_BOMB=16.

    Regression: Pillow's DecompressionBombError is NOT a subclass of ours,
    so ``except DecompressionBombError`` in the CLI used to miss it entirely
    and it would fall through to EXIT_ENGINE_ERROR=30.
    """
    from unittest.mock import patch

    import PIL.Image

    from pdf_smasher import DecompressionBombError as HankBomb
    from pdf_smasher.engine import image_export as ie

    pdf_bytes = _make_pdf(1)

    def _raise_pillow_bomb(*_args: object, **_kwargs: object) -> None:
        msg = "synthetic pillow bomb"
        raise PIL.Image.DecompressionBombError(msg)

    # Patch rasterize_page so we control exactly when the Pillow bomb fires
    # inside the generator's per-page try. We use the image_export module's
    # re-export so the patch hits the real call site.
    with patch.object(ie, "rasterize_page", _raise_pillow_bomb), pytest.raises(HankBomb):
        list(
            iter_pages_as_images(
                pdf_bytes,
                page_indices=[0],
                image_format="jpeg",
                dpi=72,
            ),
        )


def test_iter_pages_rejects_excessive_dpi_at_library_level() -> None:
    """Library callers must not bypass the --image-dpi cap.

    The CLI enforces --image-dpi <= 1200 at argparse time, but library
    callers of iter_pages_as_images previously got no cap at all.
    """
    pdf_bytes = _make_pdf(1)
    with pytest.raises(ValueError, match=r"dpi|cap"):
        list(
            iter_pages_as_images(
                pdf_bytes,
                page_indices=[0],
                image_format="jpeg",
                dpi=5000,  # above the 1200 cap
            )
        )
