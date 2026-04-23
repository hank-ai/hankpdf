"""Tests for pdf_smasher.engine.background — background layer extraction."""

from __future__ import annotations

import numpy as np
from PIL import Image

from pdf_smasher.engine.background import extract_background


def _raster_with_dark_blob(width: int = 600, height: int = 600) -> Image.Image:
    """Mostly-cream raster with a black blob in the middle (the 'text')."""
    arr = np.full((height, width, 3), 235, dtype=np.uint8)  # cream paper
    arr[200:400, 200:400] = 20  # dark blob
    return Image.fromarray(arr)


def _mask_over_blob(width: int = 600, height: int = 600) -> Image.Image:
    arr = np.zeros((height, width), dtype=bool)
    arr[200:400, 200:400] = True
    return Image.fromarray(arr).convert("1")


def test_background_is_downsampled_to_target_dimensions() -> None:
    """If input is 600x600 at 300 DPI source and we target 150 bg DPI, output is 300x300."""
    raster = _raster_with_dark_blob()  # treat as rendered at 300 DPI
    mask = _mask_over_blob()
    bg = extract_background(raster, mask=mask, source_dpi=300, target_dpi=150)
    assert bg.size == (300, 300)


def test_target_dpi_equal_to_source_keeps_dimensions() -> None:
    raster = _raster_with_dark_blob(400, 400)
    mask = _mask_over_blob(400, 400)
    bg = extract_background(raster, mask=mask, source_dpi=300, target_dpi=300)
    assert bg.size == (400, 400)


def test_masked_region_is_inpainted_to_surrounding_color() -> None:
    """The dark text region should be replaced by the cream paper color."""
    raster = _raster_with_dark_blob()
    mask = _mask_over_blob()
    bg = extract_background(raster, mask=mask, source_dpi=300, target_dpi=300)
    arr = np.asarray(bg.convert("RGB"), dtype=np.int16)
    # At the downsampled center of where the blob was, pixels should be light
    # (>= 200), not near-black (<50).
    center = arr[300, 300]
    assert center.mean() > 180, f"inpaint failed — center still dark: {center}"


def test_background_mode_is_rgb() -> None:
    raster = _raster_with_dark_blob()
    mask = _mask_over_blob()
    bg = extract_background(raster, mask=mask, source_dpi=300, target_dpi=150)
    assert bg.mode == "RGB"


def test_empty_mask_just_downsamples() -> None:
    """When nothing needs inpainting, output should be the raster downsampled."""
    raster = _raster_with_dark_blob()
    empty_mask = Image.fromarray(np.zeros((600, 600), dtype=bool)).convert("1")
    bg = extract_background(raster, mask=empty_mask, source_dpi=300, target_dpi=150)
    # Dark blob is preserved (nothing was masked → nothing was inpainted) but
    # the image is half size.
    assert bg.size == (300, 300)
    arr = np.asarray(bg.convert("RGB"))
    # Should still have a dark region near the middle (it wasn't inpainted).
    assert arr[150, 150].mean() < 60
