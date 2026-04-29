"""Tests for hankpdf.engine.foreground — extract the foreground layer.

The foreground layer in our MRC output is a 1-bit image. Where the mask is
True, we preserve ink; where the mask is False, the background layer shows
through.

We store the 1-bit ink shape here; the ink color is recorded separately as
metadata (default: global median dark pixel color). Downstream ``compose``
combines (mask, foreground_1bit, ink_color) into the PDF's SMask + foreground
image XObject pair.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from hankpdf.engine.foreground import ForegroundLayer, extract_foreground


def _page_with_black_text_on_white(width: int = 400, height: int = 200) -> Image.Image:
    img = Image.new("RGB", (width, height), color="white")
    arr = np.array(img)
    # Simulate a black glyph blob
    arr[80:120, 150:250] = 0
    return Image.fromarray(arr)


def _white_mask(width: int = 400, height: int = 200) -> Image.Image:
    arr = np.zeros((height, width), dtype=bool)
    return Image.fromarray(arr).convert("1")


def _mask_over_text(width: int = 400, height: int = 200) -> Image.Image:
    arr = np.zeros((height, width), dtype=bool)
    arr[80:120, 150:250] = True
    return Image.fromarray(arr).convert("1")


def test_returns_foreground_layer_with_1bit_image() -> None:
    raster = _page_with_black_text_on_white()
    mask = _mask_over_text()
    result = extract_foreground(raster, mask=mask)
    assert isinstance(result, ForegroundLayer)
    assert result.image.mode == "1"
    assert result.image.size == raster.size


def test_foreground_shape_matches_mask() -> None:
    raster = _page_with_black_text_on_white()
    mask = _mask_over_text()
    fg = extract_foreground(raster, mask=mask)
    fg_arr = np.array(fg.image)
    mask_arr = np.array(mask)
    # Wherever mask is True we keep ink; everywhere else must be clear (False).
    assert not fg_arr[~mask_arr].any()


def test_ink_color_is_rgb_tuple() -> None:
    raster = _page_with_black_text_on_white()
    mask = _mask_over_text()
    fg = extract_foreground(raster, mask=mask)
    assert isinstance(fg.ink_color, tuple)
    assert len(fg.ink_color) == 3
    assert all(0 <= c <= 255 for c in fg.ink_color)


def test_ink_color_near_black_for_black_text() -> None:
    raster = _page_with_black_text_on_white()
    mask = _mask_over_text()
    fg = extract_foreground(raster, mask=mask)
    assert all(c < 50 for c in fg.ink_color), f"expected near-black ink, got {fg.ink_color}"


def test_empty_mask_returns_blank_foreground() -> None:
    raster = _page_with_black_text_on_white()
    mask = _white_mask()  # all-False
    fg = extract_foreground(raster, mask=mask)
    assert not np.array(fg.image).any()
    # ink_color can be anything sensible (default black) when mask is empty.
    assert all(0 <= c <= 255 for c in fg.ink_color)
