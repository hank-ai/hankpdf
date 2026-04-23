"""Foreground-layer extraction.

For the spike we track two things:

1. The 1-bit shape of the ink (same as the mask — where the ink lives).
2. A single ink color (global median of raster pixels where the mask is True),
   used by the PDF composer as the color of the foreground image XObject.

Per-region color (each connected component gets its own color) is a Phase-2
refinement and not needed for the ratio-feasibility spike.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from pdf_smasher.engine.strategy import LIGHT_PIXEL_VALUE
from pdf_smasher.engine.verifier import CHANNEL_SPREAD_COLOR_TOLERANCE

_DEFAULT_INK = (0, 0, 0)  # black when there's no mask coverage to sample

# Mirrors CHANNEL_SPREAD_COLOR_TOLERANCE so the mono-detector and the verifier's
# color-parity check agree on what "color" means.  Named separately to make the
# coupling explicit and testable.
_MONOCHROME_CHANNEL_SPREAD_TOLERANCE = CHANNEL_SPREAD_COLOR_TOLERANCE
_MONOCHROME_TOLERANCE_PERCENTILE = 99.0
_MONOCHROME_COLORED_PIXEL_FRACTION = 0.001
_MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX = 512


_PAPER_LIGHT_THRESHOLD = LIGHT_PIXEL_VALUE  # single source of truth; see strategy.py
_DEFAULT_PAPER_FALLBACK: tuple[int, int, int] = (255, 255, 255)


def detect_paper_color(raster: Image.Image) -> tuple[int, int, int]:
    """Return the dominant paper color as an RGB tuple.

    Samples pixels whose per-channel mean (≈luminance) is >=
    ``_PAPER_LIGHT_THRESHOLD`` and returns their per-channel mean.
    Mean is O(N) single-pass; median is O(N log N) + sort buffer — paper
    color is dominated by ~90%+ uniform pixels so results are equivalent
    but mean is ~5-10× faster and ~70 MB lighter on a 300 DPI page.

    Falls back to (255, 255, 255) when no pixels qualify.
    """
    rgb = np.asarray(raster.convert("RGB"), dtype=np.uint8)
    # Max-channel brightness: a cream pixel (230, 225, 210) has max=230 and
    # hits the threshold even though its mean (~222) would not.
    brightness = rgb.max(axis=-1)
    light_mask = brightness >= _PAPER_LIGHT_THRESHOLD
    if not light_mask.any():
        return _DEFAULT_PAPER_FALLBACK
    samples = rgb[light_mask]
    mean_rgb = samples.mean(axis=0).astype(int)
    return (int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2]))


def is_effectively_monochrome(
    raster: Image.Image,
    *,
    tolerance: int = _MONOCHROME_CHANNEL_SPREAD_TOLERANCE,
) -> bool:
    """Return True if *raster* has no meaningful color content.

    Two-pass noise-tolerant test:
    1. 99th-percentile channel spread ≤ tolerance — catches nearly-uniform tints.
    2. Fraction of "colored" pixels > 0.1% — catches small stamps / logos.

    Downsamples large images to ≤512px before analysis for speed.
    """
    if raster.mode in {"L", "1"}:
        return True
    thumb = raster.copy()
    thumb.thumbnail(
        (_MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX, _MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX),
        Image.Resampling.LANCZOS,
    )
    arr = np.asarray(thumb.convert("RGB"), dtype=np.int16)
    channel_spread = arr.max(axis=-1) - arr.min(axis=-1)
    percentile_value = float(np.percentile(channel_spread, _MONOCHROME_TOLERANCE_PERCENTILE))
    if percentile_value > tolerance:
        return False
    colored_pixel_fraction = float((channel_spread > tolerance).sum()) / channel_spread.size
    return colored_pixel_fraction <= _MONOCHROME_COLORED_PIXEL_FRACTION


@dataclass(frozen=True)
class ForegroundLayer:
    """Output of :func:`extract_foreground`."""

    image: Image.Image  # 1-bit PIL image matching raster dimensions
    ink_color: tuple[int, int, int]  # RGB, 0-255 each


def extract_foreground(
    raster: Image.Image,
    *,
    mask: Image.Image,
) -> ForegroundLayer:
    """Return the foreground layer for MRC composition."""
    mask_arr = np.asarray(mask.convert("1"), dtype=bool)

    # Sample ink color: median of RGB pixels where mask is True.
    if mask_arr.any():
        rgb = np.asarray(raster.convert("RGB"), dtype=np.uint8)
        samples = rgb[mask_arr]
        median = np.median(samples, axis=0).astype(int)
        ink_color = (int(median[0]), int(median[1]), int(median[2]))
    else:
        ink_color = _DEFAULT_INK

    # The 1-bit foreground shape is just the mask itself — the ink-shape image
    # the composer will paint in ``ink_color`` through the SMask alpha channel.
    foreground_image = Image.fromarray(mask_arr).convert("1")
    return ForegroundLayer(image=foreground_image, ink_color=ink_color)
