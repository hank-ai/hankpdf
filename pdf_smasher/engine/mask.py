"""Build a 1-bit foreground / background mask for MRC segmentation.

Algorithm (docs/KNOWLEDGE.md §2.3):

1. Grayscale the input raster.
2. Adaptive threshold (OpenCV ``adaptiveThreshold`` with Gaussian weighting) —
   finds dark pixels on a light background (thin text strokes, line art).
3. Global-dark threshold — catches solid ink blobs where adaptive
   thresholding fails (signatures, stamps, thick rules) because their
   interior has no local contrast.
4. Union the two.
5. Morphological close (3x3 kernel default) — smooths mask edges and fills
   1-px interior holes.

**Word boxes from OCR are NOT used for mask construction** — they're for
positioning the invisible OCR text layer, not for deciding which pixels are
ink. Filling rectangles at word-box locations would include whitespace
between glyphs in the mask, which bloats the foreground layer and destroys
compression quality.

Returns a PIL image in ``"1"`` mode (1 bit per pixel).
"""

from __future__ import annotations

from collections.abc import Sequence

import cv2
import numpy as np
from PIL import Image

from pdf_smasher._pillow_hardening import ensure_capped
from pdf_smasher.engine.ocr import WordBox

ensure_capped()

_ADAPTIVE_BLOCK_SIZE = 25  # must be odd; OpenCV requirement
_ADAPTIVE_C = 15  # constant subtracted from the weighted mean
_GLOBAL_DARK_THRESHOLD = 96  # absolute darkness cutoff for solid-ink regions (0-255)


def build_mask(
    image: Image.Image,
    *,
    word_boxes: Sequence[WordBox] = (),  # noqa: ARG001 — kept for API stability
    close_kernel_size: int = 3,
) -> Image.Image:
    """Return a 1-bit PIL mask where True = foreground (ink pixels)."""
    gray = np.asarray(image.convert("L"), dtype=np.uint8)

    # Adaptive threshold: find thin edges where local contrast identifies text.
    adaptive = cv2.adaptiveThreshold(
        gray,
        maxValue=255,
        adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY_INV,
        blockSize=_ADAPTIVE_BLOCK_SIZE,
        C=_ADAPTIVE_C,
    )
    # Global dark pass: catch solid-ink regions (signatures, stamps, thick
    # rules) where adaptive threshold fails because the interior has no local
    # contrast.
    global_dark = gray < _GLOBAL_DARK_THRESHOLD
    mask = (adaptive > 0) | global_dark

    # Morphological close to smooth edges + fill 1-px interior holes.
    if close_kernel_size > 1:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (close_kernel_size, close_kernel_size),
        )
        mask_u8: np.ndarray = mask.astype(np.uint8) * 255
        mask_u8 = cv2.morphologyEx(mask_u8, cv2.MORPH_CLOSE, kernel)
        mask = mask_u8 > 0

    return Image.fromarray(mask).convert("1")
