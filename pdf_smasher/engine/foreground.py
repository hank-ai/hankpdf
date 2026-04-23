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

_DEFAULT_INK = (0, 0, 0)  # black when there's no mask coverage to sample


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
