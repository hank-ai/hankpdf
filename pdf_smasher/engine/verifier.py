"""Content-preservation verifier.

Three signals:

1. **OCR Levenshtein ratio** — per-page edit-distance between input and
   output OCR text, normalized by the longer string length. Low = drift-free.
2. **Digit multiset match** — regex-extract all digit runs (including
   decimals + unit suffixes) from both OCR texts and compare as multisets.
   Catches the Xerox 6/8 substitution and lost decimal classes of bug.
3. **SSIM** — structural similarity of grayscale-rendered input vs output
   page at the same DPI.

Callers pass pre-rasterized + pre-OCR'd pages; this module is pure.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Literal

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity

from pdf_smasher.types import VerifierResult

_DIGIT_RUN_RE = re.compile(r"\d+(?:[.,]\d+)?(?:\s*(?:mg|mcg|mL|IU|ng|g|kg|lb|oz|%))?")

_DEFAULT_LEVENSHTEIN_CEILING = 0.02
_DEFAULT_SSIM_FLOOR = 0.92


def levenshtein_ratio(a: str, b: str) -> float:
    """Return the Levenshtein ratio: ``edit_distance(a, b) / max(len(a), len(b))``.

    0.0 means identical; 1.0 means totally different.
    """
    if not a and not b:
        return 0.0
    if len(a) < len(b):
        a, b = b, a
    # Now len(a) >= len(b)
    previous = list(range(len(b) + 1))
    for i, ch_a in enumerate(a, start=1):
        current = [i]
        for j, ch_b in enumerate(b, start=1):
            insert_cost = current[j - 1] + 1
            delete_cost = previous[j] + 1
            sub_cost = previous[j - 1] + (0 if ch_a == ch_b else 1)
            current.append(min(insert_cost, delete_cost, sub_cost))
        previous = current
    distance = previous[-1]
    return distance / max(len(a), len(b))


def _extract_digit_runs(text: str) -> Counter[str]:
    """Return a multiset of normalized digit-run tokens found in ``text``."""
    hits = _DIGIT_RUN_RE.findall(text)
    # Normalize whitespace inside each token.
    normed = ["".join(h.split()) for h in hits]
    return Counter(normed)


def digit_multiset_match(a: str, b: str) -> bool:
    """True iff the multisets of digit tokens in ``a`` and ``b`` are equal."""
    return _extract_digit_runs(a) == _extract_digit_runs(b)


def ssim_score(a: Image.Image, b: Image.Image) -> float:
    """Return SSIM(a, b) on grayscale, resampling if sizes differ."""
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.LANCZOS)
    a_arr = np.asarray(a.convert("L"), dtype=np.float64)
    b_arr = np.asarray(b.convert("L"), dtype=np.float64)
    # scikit-image's SSIM returns (score, [grad], [S]) when full=False.
    score = structural_similarity(a_arr, b_arr, data_range=255.0)  # type: ignore[no-untyped-call]
    return float(score)


def verify_pages(
    *,
    input_rasters: Sequence[Image.Image],
    output_rasters: Sequence[Image.Image],
    input_ocr_texts: Sequence[str],
    output_ocr_texts: Sequence[str],
    levenshtein_ceiling: float = _DEFAULT_LEVENSHTEIN_CEILING,
    ssim_floor: float = _DEFAULT_SSIM_FLOOR,
) -> VerifierResult:
    """Run all three checks per page. Return a summary :class:`VerifierResult`.

    Gate: a page passes iff Levenshtein ≤ ceiling AND digit multisets match
    AND SSIM ≥ floor. Overall status is ``"pass"`` iff every page passes.
    """
    if not (
        len(input_rasters) == len(output_rasters) == len(input_ocr_texts) == len(output_ocr_texts)
    ):
        msg = "verifier: all input sequences must have the same length"
        raise ValueError(msg)

    worst_lev = 0.0
    min_ssim_global = 1.0
    min_ssim_tile = 1.0
    all_digit_match = True
    failing_pages: list[int] = []

    for i, (in_r, out_r, in_t, out_t) in enumerate(
        zip(input_rasters, output_rasters, input_ocr_texts, output_ocr_texts, strict=True),
    ):
        lev = levenshtein_ratio(in_t, out_t)
        worst_lev = max(worst_lev, lev)
        digits_ok = digit_multiset_match(in_t, out_t)
        if not digits_ok:
            all_digit_match = False
        score = ssim_score(in_r, out_r)
        if score < min_ssim_global:
            min_ssim_global = score
            min_ssim_tile = score  # single-tile approximation for now; Phase 2 adds tile min

        if lev > levenshtein_ceiling or not digits_ok or score < ssim_floor:
            failing_pages.append(i)

    status: Literal["pass", "fail"] = "pass" if not failing_pages else "fail"
    return VerifierResult(
        status=status,
        ocr_levenshtein=worst_lev,
        ssim_global=min_ssim_global,
        ssim_min_tile=min_ssim_tile,
        digit_multiset_match=all_digit_match,
        structural_match=True,  # populated by structural audit separately
        failing_pages=tuple(failing_pages),
    )
