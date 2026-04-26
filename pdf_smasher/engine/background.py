"""Background-layer extraction.

1. Inpaint masked (foreground) pixels with surrounding-pixel values so the
   mask region doesn't show through the foreground image.
2. Downsample from ``source_dpi`` to ``target_dpi`` (default 150).

Uses OpenCV's Telea inpainting (``cv2.INPAINT_TELEA``) for the hole fill.
Simpler approach (nearest-neighbor fill) would also work for the spike, but
Telea gives noticeably better visual results when the background has any
tonal variation (paper texture, stamps) and costs negligible extra time.
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image

from pdf_smasher._pillow_hardening import ensure_capped

ensure_capped()

_INPAINT_RADIUS = 3  # pixels — small because we only fill glyph holes


def extract_background(
    raster: Image.Image,
    *,
    mask: Image.Image,
    source_dpi: int,
    target_dpi: int,
) -> Image.Image:
    """Produce the background layer: inpaint foreground, then downsample."""
    rgb = np.asarray(raster.convert("RGB"), dtype=np.uint8)
    mask_arr = np.asarray(mask.convert("1"), dtype=np.uint8) * 255

    # OpenCV expects BGR; Pillow gave us RGB. Convert in-place for inpaint.
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if mask_arr.any():
        bgr = cv2.inpaint(bgr, mask_arr, _INPAINT_RADIUS, cv2.INPAINT_TELEA)
    rgb_inpainted = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    inpainted = Image.fromarray(rgb_inpainted, mode="RGB")

    if target_dpi == source_dpi:
        return inpainted
    scale = target_dpi / source_dpi
    new_size = (
        max(1, round(raster.size[0] * scale)),
        max(1, round(raster.size[1] * scale)),
    )
    return inpainted.resize(new_size, Image.Resampling.LANCZOS)
