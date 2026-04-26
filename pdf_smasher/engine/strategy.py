"""Per-page strategy selector.

Classifies each page into one of four buckets before compression. Saves
work by routing text-only and photo-only pages to cheaper single-image
paths; MRC overhead only pays off when the page is genuinely mixed.

Classes (ARCHITECTURE §4.3.1):

- ALREADY_OPTIMIZED → pass-through. Detected via triage, not this module.
- TEXT_ONLY → single JBIG2 / CCITT image; no SMask, no bg layer.
  Triggered by low-variance background + small-to-moderate mask coverage.
- PHOTO_ONLY → single JPEG2000 / JPEG image; no fg/mask split.
  Triggered by near-zero mask coverage on a high-variance page.
- MIXED → full MRC pipeline. The default.
"""

from __future__ import annotations

from enum import Enum

import numpy as np
from PIL import Image

from pdf_smasher._pillow_hardening import ensure_capped

ensure_capped()

_MASK_COVERAGE_MIXED_FLOOR = 0.05  # below this → single-image route
LIGHT_PIXEL_VALUE = 230  # public — shared with foreground.detect_paper_color
_LIGHT_PIXEL_FRACTION_UNIFORM = 0.80  # >=80% light pixels = paper-dominated (text-like bg)


class PageStrategy(Enum):
    """Per-page codec strategy."""

    ALREADY_OPTIMIZED = "already_optimized"
    TEXT_ONLY = "text_only"
    PHOTO_ONLY = "photo_only"
    MIXED = "mixed"


def _light_pixel_fraction(raster: Image.Image) -> float:
    """Fraction of grayscale pixels brighter than ``LIGHT_PIXEL_VALUE`` — paper-like."""
    gray = np.asarray(raster.convert("L"), dtype=np.uint8)
    return float((gray >= LIGHT_PIXEL_VALUE).sum()) / gray.size


def classify_page(
    raster: Image.Image,
    *,
    mask_coverage_fraction: float,
) -> PageStrategy:
    """Return a :class:`PageStrategy` for the given page.

    Parameters
    ----------
    raster:
        The rasterized page (as from :func:`rasterize_page`).
    mask_coverage_fraction:
        Fraction of pixels identified as foreground by :func:`build_mask`.
        Must be in ``[0.0, 1.0]``.
    """
    light_frac = _light_pixel_fraction(raster)
    paper_dominated = light_frac >= _LIGHT_PIXEL_FRACTION_UNIFORM

    if mask_coverage_fraction <= _MASK_COVERAGE_MIXED_FLOOR:
        # Very little ink — text splitting wouldn't help. One image.
        return PageStrategy.PHOTO_ONLY
    if paper_dominated:
        # Ink on a paper-dominated background — text-only route; no bg layer.
        return PageStrategy.TEXT_ONLY
    return PageStrategy.MIXED
