"""Unit tests for is_effectively_monochrome (Task 1).

The detector must be noise-tolerant (JPEG artefacts, faint tints) but catch
meaningful color regions like colored stamps and logo ink.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from pdf_smasher.engine.foreground import is_effectively_monochrome


# ---------- grayscale inputs ----------


def test_mode_L_is_monochrome() -> None:
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
    """4000×5000 uniform-gray image must still classify as monochrome."""
    arr = np.full((5000, 4000, 3), 180, dtype=np.uint8)
    assert is_effectively_monochrome(Image.fromarray(arr)) is True


def test_large_color_image_still_detected() -> None:
    """4000×5000 image with a colored region is caught even after downsampling."""
    arr = np.full((5000, 4000, 3), 240, dtype=np.uint8)
    arr[2000:2500, 1500:2000] = [200, 40, 40]
    assert is_effectively_monochrome(Image.fromarray(arr)) is False


# ---------- cross-module contract ----------


def test_monochrome_tolerance_matches_verifier_constant() -> None:
    """is_effectively_monochrome and _page_has_color must use the same
    channel-spread tolerance so a mono-classified page cannot silently fail
    the verifier's color-parity check."""
    from pdf_smasher.engine.foreground import _MONOCHROME_CHANNEL_SPREAD_TOLERANCE
    from pdf_smasher.engine.verifier import CHANNEL_SPREAD_COLOR_TOLERANCE

    assert _MONOCHROME_CHANNEL_SPREAD_TOLERANCE == CHANNEL_SPREAD_COLOR_TOLERANCE
