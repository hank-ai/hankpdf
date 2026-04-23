"""Tests for pdf_smasher.engine.verifier — content-preservation gate.

Three main checks per SPEC.md §5:

1. OCR Levenshtein ratio (content drift signal)
2. Digit-multiset exact match (numeric-token integrity — dosages, $s, IDs)
3. SSIM (structural fidelity)
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from pdf_smasher.engine.verifier import (
    digit_multiset_match,
    levenshtein_ratio,
    ssim_score,
    verify_pages,
)
from pdf_smasher.types import VerifierResult

# ---------- Levenshtein ----------


def test_levenshtein_identical_strings_is_zero() -> None:
    assert levenshtein_ratio("hello", "hello") == 0.0


def test_levenshtein_completely_different() -> None:
    # "abc" vs "xyz" — 3 substitutions on length 3 = 1.0 ratio
    assert levenshtein_ratio("abc", "xyz") == 1.0


def test_levenshtein_one_char_off() -> None:
    # "hello" vs "helpo" — 1 substitution on length 5 = 0.2
    assert abs(levenshtein_ratio("hello", "helpo") - 0.2) < 1e-6


def test_levenshtein_handles_empty_strings() -> None:
    assert levenshtein_ratio("", "") == 0.0
    assert levenshtein_ratio("abc", "") == 1.0


# ---------- Digit multiset ----------


def test_digit_multiset_matches_on_identical_content() -> None:
    a = "Invoice 12345 total $664.50"
    b = "Invoice 12345 total $664.50"
    assert digit_multiset_match(a, b) is True


def test_digit_multiset_matches_with_reordered_words() -> None:
    """Order doesn't matter — only the multiset of digit runs."""
    a = "line 1: $12.50  line 2: $89.50"
    b = "line 2: $89.50  line 1: $12.50"
    assert digit_multiset_match(a, b) is True


def test_digit_multiset_fails_if_digit_changes() -> None:
    """6 becoming 8 — the classic Xerox bug — must be caught."""
    a = "total 166.50"
    b = "total 168.50"
    assert digit_multiset_match(a, b) is False


def test_digit_multiset_fails_if_decimal_lost() -> None:
    a = "dose 1.25 mg"
    b = "dose 125 mg"
    assert digit_multiset_match(a, b) is False


def test_digit_multiset_matches_with_no_digits() -> None:
    assert digit_multiset_match("plain text", "plain text") is True


# ---------- SSIM ----------


def _grayscale_image(value: int, size: tuple[int, int] = (64, 64)) -> Image.Image:
    return Image.new("L", size, color=value)


def test_ssim_identical_images_is_1() -> None:
    a = _grayscale_image(128)
    assert ssim_score(a, a) == 1.0


def test_ssim_close_images_is_near_1() -> None:
    a = _grayscale_image(128)
    b = _grayscale_image(130)
    assert ssim_score(a, b) > 0.95


def test_ssim_very_different_images_is_lower() -> None:
    a = _grayscale_image(20)
    b = _grayscale_image(230)
    # Constant-intensity images with different means still have SSIM close
    # to 1 structurally, but the luminance term drops it. Assert a loose
    # ceiling — not identical.
    assert ssim_score(a, b) < 0.95


def test_ssim_handles_size_mismatch_by_resampling() -> None:
    a = _grayscale_image(128, size=(64, 64))
    b = _grayscale_image(128, size=(32, 32))
    score = ssim_score(a, b)
    assert 0.0 <= score <= 1.0


# ---------- verify_pages (compound check) ----------


def test_verify_pages_pass_on_identical_content() -> None:
    raster = Image.new("RGB", (200, 200), color="white")
    arr = np.asarray(raster).copy()
    arr[50:80, 50:150] = 0
    text = "HELLO WORLD 12345"
    result = verify_pages(
        input_rasters=[Image.fromarray(arr)],
        output_rasters=[Image.fromarray(arr)],
        input_ocr_texts=[text],
        output_ocr_texts=[text],
    )
    assert isinstance(result, VerifierResult)
    assert result.status == "pass"
    assert result.digit_multiset_match is True


def test_verify_pages_fails_on_digit_drift() -> None:
    raster = Image.new("RGB", (200, 200), color="white")
    result = verify_pages(
        input_rasters=[raster],
        output_rasters=[raster],
        input_ocr_texts=["total 166.50"],
        output_ocr_texts=["total 168.50"],  # 6 → 8
    )
    assert result.status == "fail"
    assert result.digit_multiset_match is False
    assert 0 in result.failing_pages


# ---------- tile_ssim_min ----------

from pdf_smasher.engine.verifier import tile_ssim_min  # noqa: E402


def test_tile_ssim_identical_images_is_1() -> None:
    a = Image.new("L", (500, 500), color=128)
    assert tile_ssim_min(a, a, tile_size=50) == 1.0


def test_tile_ssim_catches_local_region_drift_that_global_ssim_hides() -> None:
    """A single 20x20 dark smear in a 1000x1000 bright image barely moves global
    SSIM but must crater tile_ssim_min."""
    a_arr = np.full((1000, 1000), 240, dtype=np.uint8)
    b_arr = a_arr.copy()
    b_arr[100:120, 100:120] = 20  # localized dark smear
    a = Image.fromarray(a_arr, mode="L")
    b = Image.fromarray(b_arr, mode="L")
    global_s = float(np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).mean())
    assert global_s < 10, "global delta really is small"
    assert tile_ssim_min(a, b, tile_size=50) < 0.9


def test_tile_ssim_blank_pages_returns_1() -> None:
    """Two identical all-white pages must return 1.0, not NaN.

    skimage.structural_similarity returns NaN for constant-variance windows.
    Without np.nan_to_num(nan=1.0), block_reduce on an all-NaN tile produces
    NaN, which propagates and incorrectly fails blank-page round-trips.
    """
    blank = Image.new("L", (300, 300), color=255)
    result = tile_ssim_min(blank, blank, tile_size=50)
    assert result == 1.0, f"identical blank pages must score 1.0, got {result!r}"


# ---------- threshold constants ----------


def test_verifier_default_ssim_floor_matches_arch() -> None:
    """ARCHITECTURE.md §5 (table): global SSIM >=0.92 in BOTH modes."""
    from pdf_smasher.engine.verifier import _DEFAULT_SSIM_FLOOR

    assert _DEFAULT_SSIM_FLOOR == 0.92


def test_verifier_tile_ssim_floors() -> None:
    """ARCHITECTURE.md §5 (table): tile-min SSIM >=0.85 standard, >=0.88 safe."""
    from pdf_smasher.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
    )

    assert _DEFAULT_TILE_SSIM_FLOOR_STANDARD == 0.85
    assert _DEFAULT_TILE_SSIM_FLOOR_SAFE == 0.88


def test_verifier_lev_ceilings() -> None:
    """ARCHITECTURE.md §5 (table): raw Levenshtein <=0.05 standard, <=0.02 safe."""
    from pdf_smasher.engine.verifier import (
        _DEFAULT_LEVENSHTEIN_CEILING_SAFE,
        _DEFAULT_LEVENSHTEIN_CEILING_STANDARD,
    )

    assert _DEFAULT_LEVENSHTEIN_CEILING_STANDARD == 0.05
    assert _DEFAULT_LEVENSHTEIN_CEILING_SAFE == 0.02


# ---------- _VerifierAggregator ----------


def test_verifier_aggregator_propagates_color_loss() -> None:
    """_VerifierAggregator.result() must propagate color_preserved=False
    from a single failing page even when all other pages pass."""
    from pdf_smasher.engine.verifier import PageVerdict, _VerifierAggregator

    agg = _VerifierAggregator()
    for i in range(2):
        agg.merge(
            i,
            PageVerdict(
                page_index=i,
                passed=True,
                lev=0.0,
                ssim_global=0.95,
                ssim_tile_min=0.90,
                digits_match=True,
                color_preserved=True,
            ),
        )
    agg.merge(
        2,
        PageVerdict(
            page_index=2,
            passed=False,
            lev=0.0,
            ssim_global=0.93,
            ssim_tile_min=0.87,
            digits_match=True,
            color_preserved=False,
        ),
    )
    result = agg.result()
    assert result.status == "fail"
    assert result.color_preserved is False
    assert 2 in result.failing_pages


def test_verifier_aggregator_all_pass_returns_ok() -> None:
    from pdf_smasher.engine.verifier import PageVerdict, _VerifierAggregator

    agg = _VerifierAggregator()
    for i in range(3):
        agg.merge(
            i,
            PageVerdict(
                page_index=i,
                passed=True,
                lev=0.01,
                ssim_global=0.95,
                ssim_tile_min=0.88,
                digits_match=True,
                color_preserved=True,
            ),
        )
    result = agg.result()
    assert result.status == "pass"
    assert result.color_preserved is True
    assert result.failing_pages == ()
