"""Unit tests for is_effectively_monochrome (Task 1).

The detector must be noise-tolerant (JPEG artefacts, faint tints) but catch
meaningful color regions like colored stamps and logo ink.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from hankpdf.engine.foreground import is_effectively_monochrome

# ---------- grayscale inputs ----------


def test_mode_l_is_monochrome() -> None:
    img = Image.new("L", (100, 100), 128)
    assert is_effectively_monochrome(img) is True


def test_mode_1_is_monochrome() -> None:
    img = Image.new("1", (100, 100), 0)
    assert is_effectively_monochrome(img) is True


def test_rgb_uniform_gray_is_monochrome() -> None:
    arr = np.full((200, 200, 3), 200, dtype=np.uint8)
    assert is_effectively_monochrome(Image.fromarray(arr)) is True


# ---------- color inputs ----------


def test_solid_red_is_not_monochrome() -> None:
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    arr[:, :, 0] = 220
    assert is_effectively_monochrome(Image.fromarray(arr)) is False


def test_small_color_stamp_is_not_monochrome() -> None:
    """Colored stamp covering ~0.3% of a letter-sized page must be detected."""
    arr = np.full((2550, 3300, 3), 240, dtype=np.uint8)
    arr[1000:1159, 1000:1159] = [200, 40, 40]  # ~0.3% of pixels
    assert is_effectively_monochrome(Image.fromarray(arr)) is False


def test_jpeg_ringing_halos_are_tolerated() -> None:
    """JPEG ringing artefacts (channel spread ≤ 15) on 5% of pixels must NOT
    trigger color detection."""
    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    rng = np.random.default_rng(seed=1)
    halo_mask = rng.random((300, 300)) < 0.05
    arr[halo_mask, 0] = 230  # spread=10 < tolerance=15
    arr[halo_mask, 2] = 240
    assert is_effectively_monochrome(Image.fromarray(arr)) is True


# ---------- large image downsampling ----------


def test_large_grayscale_rgb_image_downsamples_correctly() -> None:
    """4000x5000 uniform-gray image must still classify as monochrome."""
    arr = np.full((5000, 4000, 3), 180, dtype=np.uint8)
    assert is_effectively_monochrome(Image.fromarray(arr)) is True


def test_large_color_image_still_detected() -> None:
    """4000x5000 image with a colored region is caught even after downsampling."""
    arr = np.full((5000, 4000, 3), 240, dtype=np.uint8)
    arr[2000:2500, 1500:2000] = [200, 40, 40]
    assert is_effectively_monochrome(Image.fromarray(arr)) is False


# ---------- cross-module contract ----------


def test_monochrome_tolerance_matches_verifier_constant() -> None:
    """is_effectively_monochrome and _page_has_color must use the same
    channel-spread tolerance so a mono-classified page cannot silently fail
    the verifier's color-parity check."""
    from hankpdf.engine.foreground import _MONOCHROME_CHANNEL_SPREAD_TOLERANCE
    from hankpdf.engine.verifier import CHANNEL_SPREAD_COLOR_TOLERANCE

    assert _MONOCHROME_CHANNEL_SPREAD_TOLERANCE == CHANNEL_SPREAD_COLOR_TOLERANCE


# ---------- detect_paper_color (Task 2) ----------

from hankpdf.engine.foreground import detect_paper_color  # noqa: E402


def test_detect_paper_color_returns_rgb_tuple() -> None:
    arr = np.full((200, 200, 3), 245, dtype=np.uint8)
    result = detect_paper_color(Image.fromarray(arr))
    assert isinstance(result, tuple)
    assert len(result) == 3
    assert all(0 <= c <= 255 for c in result)


def test_detect_paper_color_matches_dominant_light_color() -> None:
    """Cream paper (245, 240, 220) with black text — detect ~(245, 240, 220)."""
    arr = np.full((300, 300, 3), [245, 240, 220], dtype=np.uint8)
    arr[80:120, 80:220] = 0  # black text
    result = detect_paper_color(Image.fromarray(arr))
    assert abs(result[0] - 245) <= 5
    assert abs(result[1] - 240) <= 5
    assert abs(result[2] - 220) <= 5


def test_detect_paper_color_falls_back_to_white_when_no_light_pixels() -> None:
    """Entirely dark page: default to white."""
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    result = detect_paper_color(Image.fromarray(arr))
    assert result == (255, 255, 255)


def test_detect_paper_color_uses_same_threshold_as_strategy_classify() -> None:
    """Both code paths must agree on 'what counts as paper' (Wave 1 B.C4)."""
    from hankpdf.engine.foreground import _PAPER_LIGHT_THRESHOLD
    from hankpdf.engine.strategy import LIGHT_PIXEL_VALUE

    assert LIGHT_PIXEL_VALUE == _PAPER_LIGHT_THRESHOLD


def test_detect_paper_color_cream_stock_exactly_at_boundary() -> None:
    """A page at RGB (230, 225, 210) is at the threshold — must be detected."""
    arr = np.full((200, 200, 3), [230, 225, 210], dtype=np.uint8)
    result = detect_paper_color(Image.fromarray(arr))
    assert result[0] == 230
    assert result[1] == 225
    assert result[2] == 210
