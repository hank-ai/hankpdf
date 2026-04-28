"""Tests for hankpdf.engine.mask — 1-bit mask construction.

The mask identifies ink pixels vs paper pixels. Foreground ink gets lossless
compression; the rest becomes aggressively-downsampled background. A good
mask = good compression + crisp text.

Word boxes are NOT used for mask construction (they're for the OCR text
layer only) — filling word-box rectangles would include whitespace between
glyphs in the mask, bloating the foreground and destroying compression.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from hankpdf.engine.mask import build_mask
from hankpdf.engine.ocr import WordBox


def _white_canvas(width: int = 400, height: int = 200) -> Image.Image:
    return Image.new("RGB", (width, height), color="white")


def _black_rect_canvas(width: int = 400, height: int = 200) -> Image.Image:
    """Canvas with a single filled black rectangle — 'ink' region for threshold."""
    img = Image.new("RGB", (width, height), color="white")
    arr = np.array(img)
    arr[50:150, 100:300] = 0  # a big black rectangle
    return Image.fromarray(arr)


def test_mask_returns_1bit_image_matching_input_dims() -> None:
    img = _white_canvas(width=400, height=200)
    mask = build_mask(img)
    assert mask.size == (400, 200)
    assert mask.mode == "1"


def test_blank_white_image_produces_empty_mask() -> None:
    img = _white_canvas()
    mask = build_mask(img)
    arr = np.array(mask)
    assert arr.sum() == 0  # no foreground anywhere


def test_white_image_with_word_boxes_produces_empty_mask() -> None:
    """Word boxes on a white page must NOT add foreground. A white page has no ink."""
    img = _white_canvas(400, 200)
    boxes = [WordBox(text="X", x=100, y=50, width=80, height=30, confidence=95.0)]
    mask = build_mask(img, word_boxes=boxes)
    arr = np.array(mask)
    assert arr.sum() == 0


def test_adaptive_threshold_catches_solid_ink() -> None:
    """A solid black rectangle gets caught by the global-dark pass."""
    img = _black_rect_canvas()
    mask = build_mask(img)
    arr = np.array(mask)
    assert arr[70:130, 120:280].sum() > 0.5 * arr[70:130, 120:280].size


def test_mask_catches_dark_ink_on_light_paper() -> None:
    """Small dark marks on a cream paper background become foreground pixels."""
    arr = np.full((200, 400, 3), 235, dtype=np.uint8)  # cream paper
    arr[80:100, 150:250] = 30  # small dark mark
    img = Image.fromarray(arr)
    mask = build_mask(img)
    m_arr = np.array(mask)
    # The dark region should be mostly foreground.
    dark_region = m_arr[80:100, 150:250]
    assert dark_region.sum() > 0.5 * dark_region.size
    # Far-away light region should be background.
    assert m_arr[10:30, 10:30].sum() == 0


def test_morphological_close_smooths_mask_edges() -> None:
    """Passing ``close_kernel_size > 1`` fills tiny 1-pixel gaps in ink regions."""
    arr = np.full((200, 400, 3), 235, dtype=np.uint8)
    # Dark ink with a 1-pixel-wide white gap
    arr[80:120, 150:250] = 30
    arr[80:120, 199:201] = 235  # gap
    img = Image.fromarray(arr)
    mask_closed = build_mask(img, close_kernel_size=3)
    arr_closed = np.array(mask_closed)
    # Gap region should still be mostly foreground after morphological close.
    gap = arr_closed[85:115, 198:202]
    assert gap.sum() > 0.5 * gap.size
