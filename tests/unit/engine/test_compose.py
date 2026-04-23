"""Tests for pdf_smasher.engine.compose — build a MRC PDF page.

Assembles (mask, foreground, background, page_size) into a single-page PDF.
Uses pikepdf. Output is raw PDF bytes.

Roundtrip test: open the output with pypdfium2 and rasterize it; assert
visible content (foreground ink appears dark, background matches where no
ink).
"""

from __future__ import annotations

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

from pdf_smasher.engine.compose import compose_mrc_page


def _black_blob_foreground(width: int = 600, height: int = 600) -> Image.Image:
    arr = np.zeros((height, width), dtype=bool)
    arr[200:400, 200:400] = True
    return Image.fromarray(arr).convert("1")


def _blank_background(width: int = 300, height: int = 300) -> Image.Image:
    return Image.new("RGB", (width, height), color=(240, 235, 220))  # cream


def _blank_mask(width: int = 600, height: int = 600) -> Image.Image:
    arr = np.zeros((height, width), dtype=bool)
    arr[200:400, 200:400] = True
    return Image.fromarray(arr).convert("1")


def test_compose_returns_bytes() -> None:
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    assert isinstance(out, bytes)
    assert out.startswith(b"%PDF-")


def test_compose_output_is_single_page() -> None:
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        assert len(pdf) == 1
    finally:
        pdf.close()


def test_compose_page_size_matches_requested() -> None:
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        w, h = pdf[0].get_size()
        assert round(w) == 612
        assert round(h) == 792
    finally:
        pdf.close()


def test_compose_rasterized_output_has_dark_ink_in_masked_region() -> None:
    """Round-trip: compose, rasterize, verify foreground ink is visible and dark."""
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        page = pdf[0]
        bitmap = page.render(scale=100 / 72)  # 100 DPI
        img = bitmap.to_pil().convert("RGB")
    finally:
        pdf.close()

    arr = np.asarray(img, dtype=np.int16)
    # At the center of the page (where the blob sits in user coordinates),
    # expect dark foreground.
    cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
    center_sample = arr[cy - 10 : cy + 10, cx - 10 : cx + 10].mean(axis=(0, 1))
    assert center_sample.mean() < 100, (
        f"expected dark foreground in masked region; got RGB {center_sample}"
    )


def test_compose_rasterized_output_has_background_where_no_ink() -> None:
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    pdf = pdfium.PdfDocument(out)
    try:
        page = pdf[0]
        img = page.render(scale=100 / 72).to_pil().convert("RGB")
    finally:
        pdf.close()
    arr = np.asarray(img, dtype=np.int16)
    # A corner pixel (well outside the central blob) should be the cream bg.
    corner = arr[10, 10]
    assert corner.mean() > 180, f"expected cream bg at corner; got RGB {corner}"


def test_compose_output_has_no_fg_xobject_when_using_imagemask() -> None:
    """With the /ImageMask optimization, the mask is painted directly in the
    ink color — no separate foreground image XObject is needed."""
    import io

    import pikepdf

    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    with pikepdf.open(io.BytesIO(out)) as pdf_obj:
        page = pdf_obj.pages[0]
        xobjects = page.Resources.get("/XObject")
        assert xobjects is not None
        names = {str(k) for k in xobjects.keys()}
        # Background must be present.
        assert "/BG" in names
        # /FG as a separate foreground image XObject must NOT exist — the
        # mask XObject (/MASK) carries the foreground via /ImageMask.
        assert "/FG" not in names


# ---------- bg_color_mode (Task 3) ----------

import io  # noqa: E402

import pikepdf  # noqa: E402


def test_mrc_uses_devicegray_when_bg_color_mode_is_grayscale() -> None:
    """bg_color_mode='grayscale' → DeviceGray JPEG in the output PDF."""
    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_color_mode="grayscale",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceGray"


def test_mrc_uses_devicergb_when_bg_color_mode_is_rgb() -> None:
    """bg_color_mode='rgb' → DeviceRGB even on a gray-looking bg."""
    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_color_mode="rgb",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceRGB"


def test_mrc_default_bg_color_mode_is_rgb() -> None:
    """Default preserves existing RGB behavior; caller explicitly opts into grayscale."""
    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceRGB"


# ---------- bg_codec (Task 7) ----------


def _openjpeg_available() -> bool:
    """Probe Pillow's JPEG2000 encoder using the SAME parameters compose uses.

    Old Pillow-SIMD builds have OpenJPEG but not quality_mode='rates' — a
    probe that only checks `format="JPEG2000"` passes, but the real encode
    raises TypeError. Use the exact params here.
    """
    import io as _io

    try:
        buf = _io.BytesIO()
        Image.new("RGB", (4, 4)).save(
            buf, format="JPEG2000", quality_mode="rates", quality_layers=[25],
        )
        return True
    except (OSError, KeyError, TypeError):
        return False


import pytest  # noqa: E402


@pytest.mark.skipif(
    not _openjpeg_available(),
    reason="Pillow build lacks OpenJPEG — bg_codec='jpeg2000' falls back to JPEG",
)
def test_compose_mrc_jpeg2000_bg_option() -> None:
    """bg_codec='jpeg2000' produces a /JPXDecode-filtered background."""
    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_codec="jpeg2000",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/Filter")) == "/JPXDecode"


def test_bg_jpeg_quality_affects_output_size() -> None:
    """Lower bg_jpeg_quality produces smaller output — confirms the knob works."""
    bg = _blank_background()
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out_hi = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_jpeg_quality=80,
    )
    out_lo = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_jpeg_quality=20,
    )
    assert len(out_lo) < len(out_hi)


def test_bg_chroma_subsampling_affects_output_size() -> None:
    """4:2:0 subsampling produces smaller JPEG than 4:4:4 on color content."""
    rng = np.random.default_rng(seed=5)
    bg = Image.fromarray(rng.integers(0, 256, size=(600, 600, 3), dtype=np.uint8))
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out_444 = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_subsampling=0,
    )
    out_420 = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_subsampling=2,
    )
    assert len(out_420) < len(out_444)


def test_jpeg2000_produces_smaller_bg_than_jpeg_on_paper_texture() -> None:
    """JPEG2000 must produce a strictly smaller output than JPEG on paper
    texture when available — otherwise the option is not worth wiring.
    Skip (via equality fallthrough) if OpenJPEG is unavailable: compose
    falls back to JPEG and both outputs equal by construction.
    """
    arr = np.full((600, 600, 3), 240, dtype=np.uint8)
    rng = np.random.default_rng(seed=1)
    noise = rng.integers(-15, 16, size=arr.shape, dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()

    out_jpeg = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_codec="jpeg",
    )
    out_jpx = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_codec="jpeg2000",
    )
    if out_jpeg != out_jpx:
        assert len(out_jpx) < len(out_jpeg) * 0.95, (
            f"JPEG2000 must be >=5% smaller when available: "
            f"jpeg={len(out_jpeg):,}, jpx={len(out_jpx):,}"
        )
