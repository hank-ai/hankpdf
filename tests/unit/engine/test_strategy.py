"""Tests for hankpdf.engine.strategy — per-page compression strategy selector.

Decides for each page whether to: pass-through (already optimized), run
MRC (the default mixed pipeline), emit a single-image text-only encoding
(very sparse color), or emit a single-image photo-only encoding (no text).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from hankpdf.engine.strategy import PageStrategy, classify_page


def _white_page(width: int = 400, height: int = 400) -> Image.Image:
    return Image.new("RGB", (width, height), color="white")


def _black_rectangle(width: int = 400, height: int = 400) -> Image.Image:
    """Mostly-white with a small black rectangle — looks like sparse text/line-art."""
    arr = np.full((height, width, 3), 255, dtype=np.uint8)
    arr[100:110, 100:300] = 0  # thin horizontal bar
    return Image.fromarray(arr)


def _photo(width: int = 400, height: int = 400) -> Image.Image:
    """High-entropy gradient — no obvious text, no large white areas."""
    x = np.linspace(0, 255, width, dtype=np.int32)
    y = np.linspace(0, 255, height, dtype=np.int32)
    xx, yy = np.meshgrid(x, y)
    arr = np.stack([xx, yy, (xx + yy) % 256], axis=-1).astype(np.uint8)
    return Image.fromarray(arr)


def _mostly_text(width: int = 400, height: int = 400) -> Image.Image:
    """White with lots of scattered thin dark marks — simulated text."""
    rng = np.random.default_rng(seed=0)
    arr = np.full((height, width, 3), 255, dtype=np.uint8)
    for _ in range(80):
        y0 = int(rng.integers(0, height - 10))
        x0 = int(rng.integers(0, width - 30))
        arr[y0 : y0 + 5, x0 : x0 + 25] = 0
    return Image.fromarray(arr)


# ----- Baseline: returns a valid PageStrategy -----


def test_returns_page_strategy() -> None:
    raster = _white_page()
    result = classify_page(raster, mask_coverage_fraction=0.0)
    assert isinstance(result, PageStrategy)


# ----- Blank page -----


def test_blank_page_is_photo_only() -> None:
    """A page with no mask coverage at all goes to the single-image path."""
    raster = _white_page()
    result = classify_page(raster, mask_coverage_fraction=0.0)
    assert result == PageStrategy.PHOTO_ONLY


# ----- Text-only (mostly mask coverage is small but some ink) -----


def test_text_only_page() -> None:
    """A page with a small fraction of mask coverage AND near-uniform bg is text-only."""
    raster = _mostly_text()
    result = classify_page(raster, mask_coverage_fraction=0.15)
    assert result == PageStrategy.TEXT_ONLY


# ----- Photo-only (lots of content but no ink-vs-paper structure) -----


def test_photo_only_page() -> None:
    """A page with near-zero mask coverage on high-variance content is photo-only."""
    raster = _photo()
    result = classify_page(raster, mask_coverage_fraction=0.01)
    assert result == PageStrategy.PHOTO_ONLY


# ----- Mixed (the default MRC case) -----


def test_mixed_page() -> None:
    """Moderate mask coverage with high-variance background signals mixed content."""
    # Use a photo-like bg but claim significant mask coverage (text overlaid).
    raster = _photo()
    result = classify_page(raster, mask_coverage_fraction=0.20)
    assert result == PageStrategy.MIXED


# ----- Tunable thresholds -----


def test_boundary_mask_coverage_0_05_is_photo_only() -> None:
    """5% mask coverage (boundary) classifies as photo-only by design."""
    raster = _photo()
    result = classify_page(raster, mask_coverage_fraction=0.05)
    assert result == PageStrategy.PHOTO_ONLY


def test_very_high_mask_coverage_on_uniform_bg_is_text_only() -> None:
    """>90% mask on uniform background = solid dark pages = still text-only route."""
    raster = _white_page()
    result = classify_page(raster, mask_coverage_fraction=0.95)
    assert result == PageStrategy.TEXT_ONLY
