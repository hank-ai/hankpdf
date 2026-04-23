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
