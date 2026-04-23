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
