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
from dataclasses import dataclass

import numpy as np
from PIL import Image
from skimage.measure import block_reduce
from skimage.metrics import structural_similarity

from pdf_smasher.types import VerifierResult

_DIGIT_RUN_RE = re.compile(r"\d+(?:[.,]\d+)?(?:\s*(?:mg|mcg|mL|IU|ng|g|kg|lb|oz|%))?")

_DEFAULT_SSIM_FLOOR = 0.92  # ARCH §5 global, both modes
_DEFAULT_TILE_SSIM_FLOOR_STANDARD = 0.85  # ARCH §5 tile, standard
_DEFAULT_TILE_SSIM_FLOOR_SAFE = 0.88  # ARCH §5 tile, safe
_DEFAULT_LEVENSHTEIN_CEILING_STANDARD = 0.05  # ARCH §5 raw Lev, standard
_DEFAULT_LEVENSHTEIN_CEILING_SAFE = 0.02  # ARCH §5 raw Lev, safe

# SHARED CHANNEL-SPREAD TOLERANCE.
# Both the verifier's channel-parity check AND foreground.is_effectively_monochrome
# import this constant. If they disagree on "what counts as color", a page can route
# to TEXT_ONLY by the mono detector and then pass the verifier — resulting in silent
# color loss. Raised to 15 to defeat JPEG ringing halos (spread 5-12) around glyphs.
CHANNEL_SPREAD_COLOR_TOLERANCE = 15

# Fraction of color pixels above which the page is unambiguously colored.
_COLOR_PIXEL_FRACTION_THRESHOLD = 0.001
# Minimum connected-component area (px) that still triggers color detection
# when the fraction threshold doesn't — catches small stamps / logos.
_COLOR_COMPONENT_MIN_AREA = 200


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


def _page_has_color(raster: Image.Image) -> bool:
    """Return True if >0.1% of pixels have channel spread > tolerance OR
    any connected color region spans >=200 contiguous pixels.

    0.1% fraction catches pervasive tints. The connected-component check
    catches small but meaningful color regions (stamps, logos) that fall
    below the fraction threshold. JPEG ringing (spread < 15, no contiguous
    >=200px region) does not trigger either check.
    """
    if raster.mode in {"L", "1"}:
        return False
    arr = np.asarray(raster.convert("RGB"), dtype=np.int16)
    spread = arr.max(axis=-1) - arr.min(axis=-1)
    color_mask = spread > CHANNEL_SPREAD_COLOR_TOLERANCE
    color_frac = float(color_mask.sum()) / color_mask.size
    if color_frac > _COLOR_PIXEL_FRACTION_THRESHOLD:
        return True
    import cv2 as _cv2  # noqa: PLC0415

    _, _, stats, _ = _cv2.connectedComponentsWithStats(
        color_mask.astype(np.uint8),
        connectivity=8,
    )
    # stats[0] is background label — skip it. Column 4 = CC_STAT_AREA.
    if len(stats) <= 1:
        return False
    return bool(stats[1:, _cv2.CC_STAT_AREA].max() >= _COLOR_COMPONENT_MIN_AREA)


@dataclass(frozen=True)
class PageVerdict:
    """Per-page verifier outcome. Aggregator in compress() merges these."""

    page_index: int
    passed: bool
    lev: float
    ssim_global: float
    ssim_tile_min: float
    digits_match: bool
    color_preserved: bool


def verify_single_page(
    *,
    input_raster: Image.Image,
    output_raster: Image.Image,
    input_ocr_text: str,
    output_ocr_text: str,
    lev_ceiling: float,
    ssim_floor: float,
    tile_ssim_floor: float,
    check_color_preserved: bool = True,
) -> PageVerdict:
    """Run all verifier checks on a single page. Pure, no I/O, no accumulation.

    ``check_color_preserved=False`` disables the channel-parity check for
    callers that have explicitly opted into color loss (e.g., force_monochrome).
    """
    lev = levenshtein_ratio(input_ocr_text, output_ocr_text)
    digits_match = digit_multiset_match(input_ocr_text, output_ocr_text)
    global_score = ssim_score(input_raster, output_raster)
    tile_score = tile_ssim_min(input_raster, output_raster, tile_size=50)
    if check_color_preserved:
        in_color = _page_has_color(input_raster)
        out_color = _page_has_color(output_raster)
        color_preserved = not (in_color and not out_color)
    else:
        color_preserved = True
    passed = (
        lev <= lev_ceiling
        and digits_match
        and global_score >= ssim_floor
        and tile_score >= tile_ssim_floor
        and color_preserved
    )
    return PageVerdict(
        page_index=-1,  # filled in by caller
        passed=passed,
        lev=lev,
        ssim_global=global_score,
        ssim_tile_min=tile_score,
        digits_match=digits_match,
        color_preserved=color_preserved,
    )


class _VerifierAggregator:
    """Streaming per-page verdict accumulator. Holds only O(1) scalars plus
    per-check failure counters so the drift error can tell the user which
    gates specifically tripped.
    """

    def __init__(self) -> None:
        self._worst_lev: float = 0.0
        self._min_ssim_global: float = 1.0
        self._min_ssim_tile: float = 1.0
        self._any_digit_mismatch: bool = False
        self._color_preserved: bool = True
        self._failing_pages: list[int] = []
        # Per-check failure counts (accumulated from thresholds applied by caller).
        self._n_digit_mismatch: int = 0
        self._n_color_loss: int = 0
        self._total_pages: int = 0

    def merge(
        self,
        page_idx: int,
        verdict: PageVerdict,
        *,
        lev_ceiling: float | None = None,  # noqa: ARG002 — reserved for per-page threshold reporting
        ssim_floor: float | None = None,  # noqa: ARG002 — reserved for per-page threshold reporting
        tile_ssim_floor: float | None = None,  # noqa: ARG002 — reserved for per-page threshold reporting
    ) -> None:
        self._worst_lev = max(self._worst_lev, verdict.lev)
        self._min_ssim_global = min(self._min_ssim_global, verdict.ssim_global)
        self._min_ssim_tile = min(self._min_ssim_tile, verdict.ssim_tile_min)
        self._total_pages += 1
        if not verdict.digits_match:
            self._any_digit_mismatch = True
            self._n_digit_mismatch += 1
        if not verdict.color_preserved:
            self._color_preserved = False
            self._n_color_loss += 1
        if not verdict.passed:
            self._failing_pages.append(page_idx)

    def failure_summary(self) -> str:
        """Human-readable breakdown of which gates tripped. Empty if clean."""
        if not self._failing_pages:
            return ""
        n_fail = len(self._failing_pages)
        parts = [f"{n_fail}/{self._total_pages} pages failed the verifier:"]
        if self._n_digit_mismatch:
            parts.append(
                f"  - {self._n_digit_mismatch} pages: digit multiset changed "
                "(characters/digits differ between input and output OCR) — "
                "most likely cause: binarization on TEXT_ONLY route dropped "
                "or distorted small glyphs. Try dropping --force-monochrome.",
            )
        if self._worst_lev > _DEFAULT_LEVENSHTEIN_CEILING_STANDARD:
            parts.append(
                f"  - OCR text edit-distance up to {self._worst_lev:.2%} "
                "(0.05 standard ceiling / 0.02 safe). Matches the digit check "
                "above on most drift scenarios.",
            )
        if self._min_ssim_global < _DEFAULT_SSIM_FLOOR:
            parts.append(
                f"  - min global SSIM {self._min_ssim_global:.4f} "
                "(0.92 floor). The rendered output looks structurally different "
                "from the input. Try --target-color-quality 75 to raise bg "
                "fidelity, or drop --mode fast if used.",
            )
        if self._min_ssim_tile < _DEFAULT_TILE_SSIM_FLOOR_STANDARD and self._min_ssim_tile > -1.0:
            parts.append(
                f"  - min tile SSIM {self._min_ssim_tile:.4f} "
                "(0.85 standard / 0.88 safe). A localized region of a MIXED "
                "page has bad JPEG artifacts. Raise --target-color-quality.",
            )
        if self._n_color_loss:
            parts.append(
                f"  - {self._n_color_loss} pages: input had color, output "
                "does not. If you passed --force-monochrome, that's expected. "
                "Otherwise there's a routing bug — please file an issue.",
            )
        return "\n".join(parts)

    def result(self) -> VerifierResult:
        return VerifierResult(
            status="pass" if not self._failing_pages else "fail",
            ocr_levenshtein=self._worst_lev,
            ssim_global=self._min_ssim_global,
            ssim_min_tile=self._min_ssim_tile,
            digit_multiset_match=not self._any_digit_mismatch,
            structural_match=True,
            color_preserved=self._color_preserved,
            failing_pages=tuple(self._failing_pages),
        )

    def skipped_result(self) -> VerifierResult:
        """Return a VerifierResult explicitly marked status='skipped'.

        Uses fail-closed sentinel metrics so any code keying on e.g.
        ``result.ssim_global >= 0.92`` fails rather than seeing a fake
        perfect score. The content-preservation invariant the README
        advertises was intentionally not run — the report must make
        that distinguishable from a real pass.
        """
        return VerifierResult(
            status="skipped",
            ocr_levenshtein=1.0,
            ssim_global=0.0,
            ssim_min_tile=0.0,
            digit_multiset_match=False,
            structural_match=False,
            color_preserved=False,
            failing_pages=(),
        )


def tile_ssim_min(
    a: Image.Image,
    b: Image.Image,
    *,
    tile_size: int = 50,
) -> float:
    """Return the minimum SSIM over tile_sizextile_size tiles of (a, b).

    Vectorized: computes the full SSIM map once via structural_similarity(
    full=True), then does a single block_reduce(..., np.nanmin) to get the
    tile minimum. No Python loop over tiles. Trailing edge tiles are padded
    with np.nan and excluded from the minimum (not 1.0, which would inflate
    scores if corruption falls on a border-straddling tile).
    """
    if abs(a.width - b.width) > 1 or abs(a.height - b.height) > 1:
        msg = (
            f"page size mismatch: input {a.size}, output {b.size}. "
            "Compose path produced a different geometry than the input page — "
            "refusing to resample before SSIM (would hide CTM/crop bugs)."
        )
        raise ValueError(msg)
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.NEAREST)  # ±1px rounding only
    a_arr = np.asarray(a.convert("L"), dtype=np.float64)
    b_arr = np.asarray(b.convert("L"), dtype=np.float64)
    _, ssim_map = structural_similarity(  # type: ignore[no-untyped-call]
        a_arr,
        b_arr,
        data_range=255.0,
        full=True,
    )
    # NaN for constant-variance windows (e.g., all-white pages) → clamp to 1.0
    # so blank pages score as perfect rather than crashing the verifier.
    ssim_map = np.nan_to_num(ssim_map, nan=1.0)
    tile_mins = block_reduce(  # type: ignore[no-untyped-call]
        ssim_map,
        block_size=(tile_size, tile_size),
        func=np.nanmin,
        cval=np.nan,
    )
    return float(np.nanmin(tile_mins))


def verify_pages(
    *,
    input_rasters: Sequence[Image.Image],
    output_rasters: Sequence[Image.Image],
    input_ocr_texts: Sequence[str],
    output_ocr_texts: Sequence[str],
    levenshtein_ceiling: float = _DEFAULT_LEVENSHTEIN_CEILING_STANDARD,
    ssim_floor: float = _DEFAULT_SSIM_FLOOR,
    tile_ssim_floor: float = _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
) -> VerifierResult:
    """Run all verifier checks per page. Return a summary :class:`VerifierResult`.

    Thin wrapper around ``verify_single_page`` + ``_VerifierAggregator``.
    Overall status is ``"pass"`` iff every page passes all gates.
    """
    if not (
        len(input_rasters) == len(output_rasters) == len(input_ocr_texts) == len(output_ocr_texts)
    ):
        msg = "verifier: all input sequences must have the same length"
        raise ValueError(msg)

    agg = _VerifierAggregator()
    for i, (in_r, out_r, in_t, out_t) in enumerate(
        zip(input_rasters, output_rasters, input_ocr_texts, output_ocr_texts, strict=True),
    ):
        verdict = verify_single_page(
            input_raster=in_r,
            output_raster=out_r,
            input_ocr_text=in_t,
            output_ocr_text=out_t,
            lev_ceiling=levenshtein_ceiling,
            ssim_floor=ssim_floor,
            tile_ssim_floor=tile_ssim_floor,
        )
        agg.merge(i, verdict)
    return agg.result()
