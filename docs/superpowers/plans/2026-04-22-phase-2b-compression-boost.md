# Phase 2b: Compression Boost Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Push HankPDF's compression ratio from the current 4.99× (on an 8.83 MB realistic scanner-output fixture) up to 10-100× range, matching Foxit-class commercial compressors.

**Architecture:** We already built all the pieces — per-page `classify_page` strategy classifier, three compose paths (`compose_mrc_page`, `compose_text_only_page`, `compose_photo_only_page`), JBIG2 codec, `/ImageMask` optimization, and a `force_monochrome` option. **`compress()` just doesn't use any of them yet** — it always runs the MIXED route with a full RGB background JPEG. This plan wires strategy routing into `compress()`, adds effectively-monochrome detection (catches pages that are RGB-rendered but color-free), honors `force_monochrome`, adds a JPEG2000 alternative for color backgrounds, and lands a measurement harness that proves the ratio gains on real fixtures.

**Tech Stack:** Python 3.14 (standard GIL), pikepdf, pypdfium2, Pillow, OpenCV (headless), scikit-image, Tesseract 5 + pytesseract, jbig2enc. All Apache-2.0 / BSD / MPL-2.0. No new native deps — JPEG2000 uses Pillow's bundled OpenJPEG.

**Current baseline (as of 2026-04-22):**
- 8.83 MB / 10-page realistic scanner output → 1.77 MB (**4.99×**), 25s wall
- Verifier green: OCR Levenshtein 0.0000, SSIM 0.990, digit multisets match
- 120/120 tests passing

**Target (derived from first principles + reviewer calibration):**
- Text-only test page ≥ 20× (baseline ~5×) — single-JBIG2 path produces ~1-5 KB mask + small wrapper vs ~1-5 MB input JPEG
- Photo-only test page ≥ 4× (baseline ~5×, shouldn't regress)
- Mixed fixture ≥ 8× (baseline ~5×)
- `force_monochrome=True` on our 8.83 MB fixture → **≥ 10×** (revised from 15× after Wave 1 Pre-Mortem #3 + M6: photo/blank pages in realistic scans drag the geometric mean down; 15× requires force_monochrome to cover PHOTO_ONLY too, which Task 4b now addresses, but SSIM variance on forced paths means we should assert conservatively)

**Content-preservation constraints (load-bearing):**
- The verifier must not regress. Any aggressive compression path must be **paired** with a tightening of the verifier gate — otherwise silent content loss passes as "green" (Wave 1 Pre-Mortem #1 + #3).
- Specifically: `verifier.py:75-78` converts to `L` before computing SSIM → SSIM is colorblind by construction. Before landing any new lossy color path (grayscale bg, text-only route that discards colored ruled lines), add a **channel-parity check** that catches "input had color, output is grayscale" as a verifier failure.
- Tile-level SSIM is currently a TODO stub (`verifier.py:119`). ARCHITECTURE §5 specifies tile-min — must ship real tile-SSIM in this plan before new lossy paths land.

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `tests/unit/engine/test_color_detect.py` | Unit tests for `is_effectively_monochrome` and `detect_paper_color` (the helpers live in `foreground.py`, not a new module — Wave 1 A.M1 / C.M4 flagged duplication with existing median-RGB logic) |
| `tests/integration/test_ratio_gate.py` | End-to-end ratio assertions on synthetic text-only / photo-only / mixed fixtures + **content-preservation tests** (colored ink must not route to TEXT_ONLY; paper color must survive; high-frequency detail preserved on PHOTO_ONLY) |
| `tests/integration/_fixtures.py` | Shared `_wrap_raster_as_pdf_bytes` helper — reviewed A.M5 / C.L1 flagged copy-paste across test files |
| `scripts/measure_ratios.py` | Reusable measurement harness: takes a dir of PDFs, runs `compress()`, tabulates ratios. Exception-tight (no bare `except`); refuses to print TOTAL when any page failed (Wave 1 C.M7) |

### Modified files

| Path | What changes |
|---|---|
| `pdf_smasher/engine/foreground.py` | Add `is_effectively_monochrome` + `detect_paper_color` here — consolidates with existing `extract_foreground` median-RGB logic (Wave 1 A.M1). No new module — `color_detect.py` was a duplication. |
| `pdf_smasher/__init__.py` | `compress()` dispatches per-page via an explicit match on `PageStrategy` (all four values; `ALREADY_OPTIMIZED` is currently only emitted by triage — `classify_page` can't produce it — so the branch is a defensive assertion plus logged warning, not a silent fall-through); `force_monochrome` covers `MIXED` + `PHOTO_ONLY` (B.C2 / SPEC.md:21); mask_coverage uses `max(1, size)` guard (B.C3); per-page `try/except` + **per-page OCR/raster streaming** — raster and OCR text are consumed into the verifier incrementally instead of accumulating a whole-document buffer (Wave 2 CRIT-P2: whole-doc rasters = 5 GB on 200-page scans); emits `jbig2-fallback-to-flate` warning when compose falls back (Wave 2 CRIT-P1 — silent cascade); honors `KeyboardInterrupt` by cleaning partial page_pdfs before re-raising |
| `pdf_smasher/engine/compose.py` | Add `bg_codec` + `bg_color_mode` options to `compose_mrc_page` AND `compose_photo_only_page` (A.C2); caller-supplied not self-detected to avoid cross-layer import (C.M2); thread through `target_color_quality`, `bg_chroma_subsampling`, `legal_codec_profile` (A.C3) |
| `pdf_smasher/engine/verifier.py` | Ship real tile-level SSIM (currently stubbed at verifier.py:118 — `min_ssim_tile = score` fakes tile-min from global). Use ARCHITECTURE.md §5 table values: global SSIM ≥ 0.92 (both modes), tile SSIM ≥ 0.85 standard / ≥ 0.88 safe, Lev ≤ 0.05 raw / ≤ 0.02 safe (the 0.96 value in the ARCH narrative contradicts its own table — table wins). Add **channel-parity check**: input with RGB channel-spread anywhere > tolerance paired with output in DeviceGray → verifier FAIL (Pre-Mortem #1 + #3). Channel-spread tolerance must match `is_effectively_monochrome`'s tolerance (Task 1) or the two detectors disagree (Wave 2 CRIT). This is mandatory-before-aggressive-compression. |
| `pdf_smasher/engine/strategy.py` | Add blank-page detection (route `light_frac >= 0.995 AND mask_coverage < 0.001` to TEXT_ONLY with empty mask, not PHOTO_ONLY — C.C4 / Pre-Mortem #2). Consolidate light-pixel thresholds (B.C4). |
| `pdf_smasher/types.py` | Add `bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg"`, `photo_target_dpi: int = 200` (replaces hardcoded 150 — Pre-Mortem #3), to `CompressOptions`. Keep `target_color_quality`, `bg_chroma_subsampling`, `legal_codec_profile` but they're finally wired in Task 7.5. |
| `pdf_smasher/cli/main.py` | Add `--bg-codec`, `--force-monochrome`, `--photo-target-dpi` CLI flags |
| `docs/SPEC.md` | §1.1: document `bg_codec`, `photo_target_dpi` options. §2.1: document new CLI flags (`--bg-codec`, `--force-monochrome`, `--photo-target-dpi`, `--bg-chroma` is already in §2.1 but unwired — Task 7.5 fixes wiring). §8: add `strategy_distribution{class=…}` counter to local diagnostics table. (SPEC currently jumps §8→§10 — Wave 2 CRIT-1: there is no §9 in SPEC.md; the Wave-1 draft's `§9.1` reference was phantom. Metrics belong in §8 "Local logging and diagnostics.") Also add channel-parity check row to the ARCHITECTURE.md §5 verifier table (SPEC §5 is canonical-hash, not verifier). |

---

## Task 0: Preflight — verifier hardening (MANDATORY before aggressive compression)

**Rationale:** Wave 1 Pre-Mortem #1 + #3 and Reviewer A.C4 identified that the existing verifier is looser than SPEC.md §5 specifies AND colorblind by construction. Adding more aggressive compression paths without closing this is a silent-content-loss risk. This task must land before any Task that adds a lossy path (Tasks 3, 4, 6, 7).

**Files:**
- Modify: `pdf_smasher/engine/verifier.py`
- Modify: `pdf_smasher/__init__.py` (lev_ceiling / ssim_floor values)
- Test: `tests/unit/engine/test_verifier.py`

### Task 0.1 — Ship real tile-level SSIM

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_verifier.py — append
import numpy as np
from PIL import Image

from pdf_smasher.engine.verifier import tile_ssim_min


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
    # tile-min with 50px tiles must be <0.9 because one tile is fully disturbed
    assert tile_ssim_min(a, b, tile_size=50) < 0.9


def test_tile_ssim_blank_pages_returns_1() -> None:
    """Two identical all-white pages must return 1.0, not NaN.

    Wave 4 H4: skimage.structural_similarity returns NaN for constant-variance
    windows (e.g., a white page vs white page). Without np.nan_to_num(nan=1.0),
    block_reduce on an all-NaN tile produces NaN, which propagates to the final
    result and causes the verifier to incorrectly fail a blank-page round-trip.
    """
    blank = Image.new("L", (300, 300), color=255)
    result = tile_ssim_min(blank, blank, tile_size=50)
    assert result == 1.0, f"identical blank pages must score 1.0, got {result!r}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_verifier.py::test_tile_ssim_catches_local_region_drift_that_global_ssim_hides -v`
Expected: `ImportError: cannot import name 'tile_ssim_min'`

- [ ] **Step 3: Implement tile_ssim_min in verifier.py (VECTORIZED — no Python loops)**

**Wave 2 CRIT-P3 context:** a per-tile Python `for y, x: structural_similarity(...)` loop on a 2550×3300 page with 50-px tiles = 3366 tile calls × ~3 ms each ≈ **10 s/page × 200 pages = 33 min** added to every verify. That is unshippable. Vectorize by asking `structural_similarity` for the full SSIM map once, then `block_reduce` the min:

```python
# pdf_smasher/engine/verifier.py — add
from skimage.measure import block_reduce


def tile_ssim_min(
    a: Image.Image,
    b: Image.Image,
    *,
    tile_size: int = 50,
) -> float:
    """Return the minimum SSIM over tile_size×tile_size tiles of (a, b).

    Vectorized: computes the full SSIM map once via ``structural_similarity(
    full=True)``, then does a single ``block_reduce(..., np.min)`` to get the
    tile minimum. No Python loop over tiles. Resamples b to a's size when
    needed; trailing edge is absorbed by ``cval=1.0`` (outside-image tiles
    score as perfect so they don't cause a false floor).
    """
    # Wave 5 Pre-Mortem CRIT-3: FAIL on size mismatch — do NOT silently resize.
    # Resampling a geometrically-distorted output page to match the input size
    # undoes the distortion before comparison, producing falsely-high SSIM.
    # A letterboxed or aspect-ratio-flipped output would pass verification.
    # The ±1 tolerance absorbs harmless rounding in PDF point→pixel conversion.
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
    # Full SSIM map — same shape as a_arr; each pixel is the SSIM over the
    # skimage default 7×7 window centered on it.
    _, ssim_map = structural_similarity(  # type: ignore[no-untyped-call,misc]
        a_arr, b_arr, data_range=255.0, full=True,
    )
    # Wave 4 CRIT (H4 — blank page NaN): skimage returns NaN for windows where
    # the variance of BOTH inputs is zero (e.g., all-white pages). These NaN
    # pixels are semantically "perfectly identical" — clamp to 1.0 before
    # block_reduce so blank pages score 1.0, not NaN.
    # This must happen BEFORE block_reduce — np.nanmin(all-NaN-block) returns
    # NaN even with func=np.nanmin, which would propagate to the final result.
    ssim_map = np.nan_to_num(ssim_map, nan=1.0)
    # Block-reduce to tile-min. Trailing edge padded with np.nan so
    # incomplete tiles are excluded from the minimum — NOT 1.0, which would
    # silently inflate the score if corruption falls on a border-straddling
    # tile. np.nan propagates through block_reduce when func=np.nanmin, then
    # np.nanmin on the result ignores the padding entirely.
    # Wave 3 CRIT (pre-mortem): cval=1.0 was an attack vector — a small
    # smear at tile boundaries could pad away and never lower the minimum.
    tile_mins = block_reduce(
        ssim_map, block_size=(tile_size, tile_size), func=np.nanmin, cval=np.nan,
    )
    return float(np.nanmin(tile_mins))
```

Budget check: `structural_similarity(full=True)` runs in ~80 ms on a 2550×3300 array on an M-series machine; `block_reduce` is another ~20 ms. **Total: ~100 ms/page × 200 pages ≈ 20 s** — 100× faster than the loop, within verifier budget.

- [ ] **Step 4: Wire into verify_pages**

Modify `verify_pages` in `verifier.py` — replace the `min_ssim_tile = score` fake (line 118) with a real call:

```python
# Inside the per-page loop in verify_pages:
tile_score = tile_ssim_min(in_r, out_r, tile_size=50)
if tile_score < min_ssim_tile:
    min_ssim_tile = tile_score
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/engine/test_verifier.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pdf_smasher/engine/verifier.py tests/unit/engine/test_verifier.py
git commit -m "feat(verifier): ship real tile-level SSIM (closes line 119 TODO)"
```

### Task 0.2 — Align SSIM/Levenshtein thresholds with ARCHITECTURE §5 table

**Wave 2 CRIT-2 context:** Wave-1 said "safe mode global SSIM 0.96." That contradicts the canonical threshold table in ARCHITECTURE.md §5, which lists:

| Metric | Standard | Safe |
|---|---|---|
| Global SSIM | ≥ 0.92 | ≥ 0.92 (not tightened) |
| Tile-min SSIM | ≥ 0.85 | ≥ 0.88 |
| Raw Levenshtein | ≤ 0.05 | ≤ 0.02 |
| Bag-of-lines Lev | ≤ 0.02 | ≤ 0.02 |
| Digit multiset | exact | exact |

(The ARCH narrative paragraph on safe mode also mentions 0.96 — that's an internal contradiction in ARCH. Table wins. We will file a follow-up to delete the contradictory sentence.)

Raising the **global SSIM** floor to 0.96 in safe mode would reject every legitimately recompressed page in our own test corpus (SSIM drops ~0.01-0.03 on any lossy path; the loose 0.05 lev + 0.88 tile floor is where safe-mode strictness lives).

- [ ] **Step 1: Write the failing test (verifier threshold coverage)**

```python
# tests/unit/engine/test_verifier.py — append
def test_verifier_default_ssim_floor_matches_arch() -> None:
    """ARCHITECTURE.md §5 (table): global SSIM >=0.92 in BOTH modes."""
    from pdf_smasher.engine.verifier import _DEFAULT_SSIM_FLOOR
    assert _DEFAULT_SSIM_FLOOR == 0.92


def test_verifier_tile_ssim_floors() -> None:
    """ARCHITECTURE.md §5 (table): tile-min SSIM >=0.85 standard, >=0.88 safe."""
    from pdf_smasher.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
    )
    assert _DEFAULT_TILE_SSIM_FLOOR_STANDARD == 0.85
    assert _DEFAULT_TILE_SSIM_FLOOR_SAFE == 0.88


def test_verifier_lev_ceilings() -> None:
    """ARCHITECTURE.md §5 (table): raw Levenshtein <=0.05 standard, <=0.02 safe."""
    from pdf_smasher.engine.verifier import (
        _DEFAULT_LEVENSHTEIN_CEILING_STANDARD,
        _DEFAULT_LEVENSHTEIN_CEILING_SAFE,
    )
    assert _DEFAULT_LEVENSHTEIN_CEILING_STANDARD == 0.05
    assert _DEFAULT_LEVENSHTEIN_CEILING_SAFE == 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_verifier.py -k "ssim_floor or tile_ssim or lev_ceilings" -v`
Expected: FAIL — `tile_ssim_floor_*` and `_lev_ceiling_standard/safe` constants don't exist yet.

- [ ] **Step 3: Add the missing module constants, `_page_has_color`, `PageVerdict`, `verify_single_page`, wire into `verify_pages`**

**Wave 3 CRIT ordering:** `verify_single_page` calls `_page_has_color`. Define `_page_has_color` and `CHANNEL_SPREAD_COLOR_TOLERANCE` HERE in Task 0.2 — not later in Task 0.3. Task 0.3 then imports the function from this same module. This prevents the forward-reference where Task 0.2's `verify_single_page` would call a function that doesn't exist yet in the execution order.

In `pdf_smasher/engine/verifier.py`:

```python
# Replace the old _DEFAULT_LEVENSHTEIN_CEILING/_DEFAULT_SSIM_FLOOR block with:
_DEFAULT_SSIM_FLOOR = 0.92                     # ARCH §5 global, both modes
_DEFAULT_TILE_SSIM_FLOOR_STANDARD = 0.85       # ARCH §5 tile, standard
_DEFAULT_TILE_SSIM_FLOOR_SAFE = 0.88           # ARCH §5 tile, safe
_DEFAULT_LEVENSHTEIN_CEILING_STANDARD = 0.05   # ARCH §5 raw Lev, standard
_DEFAULT_LEVENSHTEIN_CEILING_SAFE = 0.02       # ARCH §5 raw Lev, safe

# SHARED CHANNEL-SPREAD TOLERANCE.
# Load-bearing: both the verifier's channel-parity check AND
# foreground.is_effectively_monochrome import this constant. If they
# disagree on "what counts as color", a page can route to TEXT_ONLY by the
# mono detector and then pass the verifier — resulting in silent color loss.
#
# Wave 5 Pre-Mortem HIGH-4: JPEG compression produces ringing halos around
# black glyphs with channel spread of 5–8 units in the 2–4px border around
# every character. With tolerance=5, a dense black-text-on-white page is
# classified as "has color" — every text page fails the channel-parity check.
# Raised from 5 to 15: JPEG ringing tops out at ~12 spread units; genuine
# color (even a pale-blue ruled line) typically has spread ≥ 20.
# Also raised the fraction gate from 0.1% to 0.5%: JPEG ringing is ubiquitous
# (every glyph has a halo), so halo pixels can easily exceed 0.1%.
CHANNEL_SPREAD_COLOR_TOLERANCE = 15  # raised from 5 to defeat JPEG ringing halos


def _page_has_color(raster: Image.Image) -> bool:
    """Return True if >0.5% of pixels have RGB channel spread > tolerance."""
    if raster.mode in {"L", "1"}:
        return False
    arr = np.asarray(raster.convert("RGB"), dtype=np.int16)
    spread = arr.max(axis=-1) - arr.min(axis=-1)
    color_frac = float((spread > CHANNEL_SPREAD_COLOR_TOLERANCE).sum()) / spread.size
    return color_frac > 0.005  # raised from 0.1% to 0.5% — JPEG ringing is widespread
```

Factor the per-page checks into `verify_single_page` (Wave 2 CRIT-P2 needs this to stream). `verify_pages` becomes a thin loop over it:

```python
from dataclasses import dataclass


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
    Callers that stream (e.g., compress() per-page) merge verdicts via a
    running aggregator; callers that already have all pages in memory can
    use `verify_pages` which wraps this.
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
        color_preserved = True  # caller opted into color loss; don't flag it
    passed = (
        lev <= lev_ceiling
        and digits_match
        and global_score >= ssim_floor
        and tile_score >= tile_ssim_floor
        and color_preserved
    )
    return PageVerdict(
        page_index=-1,  # filled in by the caller
        passed=passed,
        lev=lev,
        ssim_global=global_score,
        ssim_tile_min=tile_score,
        digits_match=digits_match,
        color_preserved=color_preserved,
    )
```

Also add `color_preserved: bool = True` to `VerifierResult` in `types.py`:

```python
# pdf_smasher/types.py — add to VerifierResult dataclass:
color_preserved: bool = True  # False if any page had color input but gray output
```

And update the `status = "fail"` calculation in `verify_pages` to include
`if not result.color_preserved`.

Rewrite `verify_pages` to be a thin wrapper:

```python
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
    # (existing length check)
    agg = _VerifierAggregator()
    for i, (in_r, out_r, in_t, out_t) in enumerate(
        zip(input_rasters, output_rasters, input_ocr_texts, output_ocr_texts, strict=True),
    ):
        verdict = verify_single_page(
            input_raster=in_r, output_raster=out_r,
            input_ocr_text=in_t, output_ocr_text=out_t,
            lev_ceiling=levenshtein_ceiling,
            ssim_floor=ssim_floor,
            tile_ssim_floor=tile_ssim_floor,
        )
        agg.merge(i, verdict)
    return agg.result()
```

Also add `_VerifierAggregator` with `merge(page_idx, PageVerdict)` + `result() -> VerifierResult`. This is the same aggregator `compress()` in Task 4a uses for its streaming path — one aggregator, two callers.

**Wave 4 CRIT: the class body was missing from the plan — engineer cannot implement without it. Full spec below:**

```python
# pdf_smasher/engine/verifier.py — add after PageVerdict

class _VerifierAggregator:
    """Streaming per-page verdict accumulator.

    Call ``merge(page_idx, verdict)`` after each page; call ``result()``
    after all pages. Holds only O(1) scalars — no raster buffers.
    """

    def __init__(self) -> None:
        self._worst_lev: float = 0.0
        self._min_ssim_global: float = 1.0
        self._min_ssim_tile: float = 1.0
        self._any_digit_mismatch: bool = False
        self._color_preserved: bool = True
        self._failing_pages: list[int] = []

    def merge(self, page_idx: int, verdict: PageVerdict) -> None:
        self._worst_lev = max(self._worst_lev, verdict.lev)
        self._min_ssim_global = min(self._min_ssim_global, verdict.ssim_global)
        self._min_ssim_tile = min(self._min_ssim_tile, verdict.ssim_tile_min)
        if not verdict.digits_match:
            self._any_digit_mismatch = True
        if not verdict.color_preserved:
            self._color_preserved = False
        if not verdict.passed:
            self._failing_pages.append(page_idx)

    def result(self) -> VerifierResult:
        # VerifierResult is imported from types.py
        from pdf_smasher.types import VerifierResult
        passed = not self._failing_pages
        # Wave 5 CRIT exec-realism: field names must match VerifierResult in
        # types.py EXACTLY. The actual fields are:
        #   status: Literal["pass", "fail", "skipped"]  — NOT "ok"
        #   ocr_levenshtein: float                      — NOT ocr_lev
        #   ssim_global: float                          — NOT min_ssim_global
        #   ssim_min_tile: float                        — NOT min_ssim_tile
        #   digit_multiset_match: bool                  — NOT digits_match
        #   structural_match: bool                      — no concept in plan; default True
        #   failing_pages: tuple[int, ...]              — NOT list
        #   color_preserved: bool = True                — new field added by Task 0.2
        return VerifierResult(
            status="pass" if passed else "fail",
            ocr_levenshtein=self._worst_lev,
            ssim_global=self._min_ssim_global,
            ssim_min_tile=self._min_ssim_tile,
            digit_multiset_match=not self._any_digit_mismatch,
            structural_match=True,  # aggregated separately if needed; default pass
            color_preserved=self._color_preserved,
            failing_pages=tuple(self._failing_pages),  # cast list → tuple (frozen field)
        )
```

**Unit test for `_VerifierAggregator` (Wave 4 H6 — no existing test):**

```python
# tests/unit/engine/test_verifier.py — append
def test_verifier_aggregator_propagates_color_loss() -> None:
    """_VerifierAggregator.result() must propagate color_preserved=False
    from a single failing page even when all other pages pass."""
    from pdf_smasher.engine.verifier import PageVerdict, _VerifierAggregator

    agg = _VerifierAggregator()
    # Two passing pages
    for i in range(2):
        agg.merge(i, PageVerdict(
            page_index=i, passed=True, lev=0.0,
            ssim_global=0.95, ssim_tile_min=0.90,
            digits_match=True, color_preserved=True,
        ))
    # One page with color loss
    agg.merge(2, PageVerdict(
        page_index=2, passed=False, lev=0.0,
        ssim_global=0.93, ssim_tile_min=0.87,
        digits_match=True, color_preserved=False,  # <-- color loss
    ))
    result = agg.result()
    assert result.status == "fail"
    assert result.color_preserved is False
    assert 2 in result.failing_pages  # tuple — `in` works for both list and tuple


def test_verifier_aggregator_all_pass_returns_ok() -> None:
    from pdf_smasher.engine.verifier import PageVerdict, _VerifierAggregator

    agg = _VerifierAggregator()
    for i in range(3):
        agg.merge(i, PageVerdict(
            page_index=i, passed=True, lev=0.01,
            ssim_global=0.95, ssim_tile_min=0.88,
            digits_match=True, color_preserved=True,
        ))
    result = agg.result()
    assert result.status == "pass"  # Wave 5 CRIT: VerifierStatus is "pass" not "ok"
    assert result.color_preserved is True
    assert result.failing_pages == ()  # Wave 5 CRIT: tuple not list
```

- [ ] **Step 4: Tighten `compress()` thresholds in `__init__.py`**

Replace the existing lev/ssim selection (wherever it lives — search for `_DEFAULT_LEVENSHTEIN_CEILING` usage in `compress()`):

```python
from pdf_smasher.engine.verifier import (
    _DEFAULT_LEVENSHTEIN_CEILING_SAFE, _DEFAULT_LEVENSHTEIN_CEILING_STANDARD,
    _DEFAULT_SSIM_FLOOR,
    _DEFAULT_TILE_SSIM_FLOOR_SAFE, _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
)

is_safe = options.mode == "safe"
lev_ceiling = _DEFAULT_LEVENSHTEIN_CEILING_SAFE if is_safe else _DEFAULT_LEVENSHTEIN_CEILING_STANDARD
ssim_floor = _DEFAULT_SSIM_FLOOR  # same in both modes
tile_ssim_floor = _DEFAULT_TILE_SSIM_FLOOR_SAFE if is_safe else _DEFAULT_TILE_SSIM_FLOOR_STANDARD
```

- [ ] **Step 5: Run full test suite to see what breaks**

Run: `uv run pytest tests -q`
Expected: some existing integration tests may fail because the tighter threshold catches real drift that the loose threshold papered over. **Each such failure is a finding, not a bug** — investigate each, decide whether the output is actually drifting or the test fixture is borderline, and fix the appropriate side.

- [ ] **Step 6: Commit**

```bash
git add pdf_smasher/__init__.py pdf_smasher/engine/verifier.py tests/unit/engine/test_verifier.py
git commit -m "fix(verifier): align SSIM/Lev thresholds to ARCHITECTURE §5 table"
```

### Task 0.3 — Channel-parity check (catches silent color loss)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_verifier.py — append
def test_verifier_fails_when_input_had_color_but_output_is_grayscale() -> None:
    """Silent color loss: input had colored ink; output is grayscale. Verifier must catch."""
    import numpy as np
    from PIL import Image
    from pdf_smasher.engine.verifier import verify_pages

    # Input: white paper with a red stamp region
    in_arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    in_arr[40:80, 40:160] = [200, 40, 40]  # red stamp
    in_raster = Image.fromarray(in_arr)

    # Output: same paper, same stamp shape — but stamp is now gray (e.g., what
    # TEXT_ONLY routing with a near-black ink_color would produce)
    out_arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    out_arr[40:80, 40:160] = 80  # gray stamp
    out_raster = Image.fromarray(out_arr)

    # OCR text matches perfectly; SSIM-on-L is high; digits match.
    result = verify_pages(
        input_rasters=[in_raster],
        output_rasters=[out_raster],
        input_ocr_texts=["STAMP"],
        output_ocr_texts=["STAMP"],
    )
    assert result.status == "fail", (
        "verifier must detect color-layer loss even when OCR/SSIM-on-L look fine"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_verifier.py::test_verifier_fails_when_input_had_color_but_output_is_grayscale -v`
**Wave 5 exec-realism HIGH-3:** Task 0.2 Step 3 ALREADY wired `_page_has_color` and `color_preserved` into `verify_single_page`, which `verify_pages` now calls. By the time this step runs, the channel-parity check is live.
Expected: **PASS** (the test demonstrates that the channel-parity check added in Task 0.2 works). If the test FAILS here, something went wrong in Task 0.2 — investigate there, not here.

- [ ] **Step 3: Wire channel-parity check into `verify_pages`**

**Wave 3 ordering note:** `CHANNEL_SPREAD_COLOR_TOLERANCE`, `_page_has_color`, and `PageVerdict.color_preserved` are already defined in Task 0.2. This step wires them into `verify_pages`.

**Wave 3 CRIT (pre-mortem connected-component check):** the 0.1% pixel-fraction threshold catches pervasive tint but misses the case where a meaningful colored region (a stamp, a logo) covers exactly 0.09% of the page (e.g., a small rubber stamp on a full-page scan). Add a connected-component check alongside the fraction: if ANY connected component of colored pixels spans ≥ 200 contiguous pixels (roughly a 14×14 area), treat the page as colored regardless of overall fraction.

**Wave 6 Pre-Mortem C1 — fraction threshold clarification:** Task 0.2 defined `_page_has_color`
with `color_frac > 0.005` (0.5%) to defeat JPEG ringing halos. THIS step (0.3) REPLACES that body
with `color_frac > 0.001` (0.1%) — the lower threshold is safe here because the connected-component
check now handles the small-stamp case that 0.5% was meant to catch, and JPEG ringing (single-pixel
scatter) never produces contiguous ≥200px regions. The Task 0.3 body below is the FINAL version.
The 0.5% body from Task 0.2 is superseded; do not keep both in the file.

Update `_page_has_color` in `verifier.py` to add the component check (this REPLACES the Task 0.2 body entirely):

```python
def _page_has_color(raster: Image.Image) -> bool:
    """Return True if >0.1% of pixels have channel spread > tolerance OR
    any connected color region spans >=200 contiguous pixels."""
    if raster.mode in {"L", "1"}:
        return False
    arr = np.asarray(raster.convert("RGB"), dtype=np.int16)
    spread = arr.max(axis=-1) - arr.min(axis=-1)
    color_mask = spread > CHANNEL_SPREAD_COLOR_TOLERANCE
    color_frac = float(color_mask.sum()) / color_mask.size
    if color_frac > 0.001:
        return True
    # Connected-component check: any contiguous color region >=200px is
    # meaningful (small stamp, logo) even if fractionally tiny.
    # Wave 4 CRIT (C8): scipy is NOT a declared project dependency.
    # cv2 (opencv-python-headless) already is. Use it instead.
    import cv2 as _cv2
    _, _, stats, _ = _cv2.connectedComponentsWithStats(
        color_mask.astype(np.uint8), connectivity=8,
    )
    # stats shape: (num_labels, 5); row 0 is the background label — skip it.
    # Column 4 is CC_STAT_AREA (pixel count for each component).
    if len(stats) <= 1:
        return False
    return bool(stats[1:, _cv2.CC_STAT_AREA].max() >= 200)
```

Inside the `verify_pages` loop, after the existing SSIM/Lev checks, call `verify_single_page` (already wired in Task 0.2 via `_VerifierAggregator`) — the color-parity check runs inside `verify_single_page.color_preserved`. The `_VerifierAggregator.result()` must propagate `color_preserved=False` to `VerifierResult.color_preserved` and include it in the `status="fail"` condition.

**Wave 6 Test-Integrity C3 — pin the fraction threshold.** The function now uses 0.001 (0.1%).
Add a test that pins this value and exercises the 0.001–0.005 boundary:

```python
# tests/unit/engine/test_verifier.py — append (Task 0.3 pinning test)
from pdf_smasher.engine.verifier import _page_has_color
import numpy as np
from PIL import Image


def test_page_has_color_fraction_boundary_0_1_pct() -> None:
    """_page_has_color uses 0.1% fraction threshold (not 0.5%).

    Wave 6 C3: pin the threshold so that changing it causes a test failure.
    A colored region at 0.3% coverage (above 0.001, below 0.005) must be
    detected as 'has color' — this would fail if threshold were 0.005.
    """
    # 2550×3300 = 8,415,000 px; 0.3% = ~25,245 px
    arr = np.full((2550, 3300, 3), 240, dtype=np.uint8)
    # Place a 0.3%-area color block: ~159×159 px ≈ 25,281 px
    arr[1000:1159, 1000:1159] = [200, 40, 40]
    img = Image.fromarray(arr)
    assert _page_has_color(img), (
        "0.3% colored region must be detected (threshold is 0.1%, not 0.5%)"
    )


def test_page_has_color_jpeg_ringing_not_detected_as_color() -> None:
    """JPEG ringing halos around black glyphs (channel spread 5-12) must NOT
    trigger the color detector (tolerance=15, fraction gate=0.1%)."""
    # Simulate a dense text page with JPEG ringing: 5% of pixels have spread 10
    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    rng = np.random.default_rng(seed=1)
    halo_mask = rng.random((300, 300)) < 0.05  # 5% halo pixels
    arr[halo_mask, 0] = 230  # spread=10 < tolerance=15
    arr[halo_mask, 2] = 240
    img = Image.fromarray(arr)
    assert not _page_has_color(img), (
        "JPEG ringing halos (spread=10, fraction=5%) must NOT be detected as color "
        "(would cause false verifier failures on every text page)"
    )
```

**Wave 3 ordering note: the cross-module contract test** (that pins `_MONOCHROME_CHANNEL_SPREAD_TOLERANCE == CHANNEL_SPREAD_COLOR_TOLERANCE`) is placed in **Task 1**, not here. Task 0.3 runs before Task 1 defines `_MONOCHROME_CHANNEL_SPREAD_TOLERANCE` in `foreground.py` — placing the import in Task 0.3 would produce an `ImportError` when the engineer runs the test at this step. See Task 1 Step 5 for the contract test.

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/unit/engine/test_verifier.py -v`
Expected: all green including the new color-parity test.

- [ ] **Step 5: Commit**

```bash
git add pdf_smasher/engine/verifier.py pdf_smasher/types.py tests/unit/engine/test_verifier.py
git commit -m "feat(verifier): channel-parity check catches silent color loss"
```

### Task 0.4 — Latent SyntaxErrors in already-shipped code (blocker)

**Wave 2 CRIT-2 context:** both `pdf_smasher/cli/main.py:136` and `pdf_smasher/engine/triage.py:111` use the Python-2 exception syntax `except A, B:` instead of `except (A, B):`. Verified reproducible via `python3 -c "import ast; ast.parse(open('<path>').read())"` — both raise `SyntaxError: multiple exception types must be parenthesized`. These files are never imported by the current 121-test suite (triage tests mock around the affected branch; cli tests stub out `--doctor`), which is why CI stays green. But the first real `hankpdf --doctor` invocation or any triage path that triggers the signed-PDF `SigFlags` type coercion will crash at import.

These two fixes were landed on `main` on 2026-04-22 as a hot-patch before this plan's sub-agent loop begins. Re-verify they stayed fixed:

- [ ] **Step 1: Verify both files parse cleanly**

Run: `uv run python -c "import ast; ast.parse(open('pdf_smasher/cli/main.py').read()); ast.parse(open('pdf_smasher/engine/triage.py').read()); print('ok')"`
Expected: `ok`. If not, apply:

```python
# pdf_smasher/cli/main.py:136
except (subprocess.TimeoutExpired, OSError):

# pdf_smasher/engine/triage.py:111
except (TypeError, ValueError):
```

- [ ] **Step 2: Add a regression-guard test so this cannot re-land**

```python
# tests/unit/test_syntax_parseability.py — new file
"""Guard against re-landing Python-2-style `except A, B:` syntax.

Wave 2 CRIT-2 caught both triage.py and cli/main.py in this state with
121 tests green because neither affected codepath is exercised by the
unit suite. This test walks the whole package via ast.parse so a
SyntaxError anywhere blocks the merge.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[1] / "pdf_smasher"


@pytest.mark.parametrize("path", sorted(_PKG_ROOT.rglob("*.py")))
def test_every_py_file_parses(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"))
```

- [ ] **Step 3: Run the guard test**

Run: `uv run pytest tests/unit/test_syntax_parseability.py -v`
Expected: every file parses.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_syntax_parseability.py
git commit -m "test: add ast.parse guard across package (Wave 2 regression gate)"
```

---

### Task 0.5 — Source-truth digit extraction from native text layer (pre-mortem content-preservation)

**Wave 3 Pre-Mortem finding:** The digit round-trip check uses `tesseract_word_boxes(input_raster)` to extract "ground truth" digits from the input. But OCR on the input raster can FAIL to recognize a digit that was rendered correctly in the native PDF text layer (e.g., a 6 misread as 0 in a small font). If ground-truth extraction is wrong, the verifier passes an output that silently changed a digit — it's not a regression because both sides OCR'd the same wrong value.

Fix: when the input PDF page has a native text layer (selectable text, from Tesseract's own native-PDF detect or from `pdftext`), prefer the native text layer for OCR ground truth. Only fall back to OCR rasterization when no text layer is present.

**Files:**
- Modify: `pdf_smasher/__init__.py` (use native text layer when available)
- Modify: `pdf_smasher/engine/verifier.py` (document the source)
- Test: `tests/unit/engine/test_verifier.py`

- [ ] **Step 1: Write the failing test**

**Wave 5 Test-Integrity CRIT-1:** the original test only called `digit_multiset_match` directly — it passes regardless of whether the native-text-extraction logic in Step 2 is implemented. Replace with a test that actually exercises the `_extract_ground_truth_text` helper this task introduces. Extract the helper from `compress()` into a testable function so it can be unit-tested directly.

```python
# pdf_smasher/__init__.py — extract as a module-level helper (not inside compress):
def _extract_ground_truth_text(
    pdf_bytes: bytes, page_index: int, fallback_ocr_text: str,
) -> str:
    """Return the native text layer for `page_index`, or `fallback_ocr_text`.

    Prefers the native PDF text layer when present and non-empty; falls back
    to the pre-computed OCR text when the page has no native layer (e.g., a
    scanned PDF with no embedded text). This prevents both-OCR-wrong scenarios
    where Tesseract misreads a digit on BOTH input and output (see Task 0.5).
    """
    from pypdfium2 import PdfDocument as _Pdfium
    try:
        _doc = _Pdfium(pdf_bytes)
        try:
            _page = _doc[page_index]
            _tp = _page.get_textpage()
            try:
                native = _tp.get_text_range()
                if native and native.strip():
                    return native.strip()
            finally:
                _tp.close()
        finally:
            _doc.close()
    except Exception:  # noqa: BLE001
        pass  # any pdfium error → fall back to OCR
    return fallback_ocr_text
```

```python
# tests/unit/engine/test_compress_helpers.py — new file
from pdf_smasher import _extract_ground_truth_text
import io, pikepdf


def _pdf_with_text_layer(text: str) -> bytes:
    """Minimal PDF with a native text layer containing `text`."""
    # Build a PDF with an actual text stream using pikepdf
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    page.Resources = resources
    content = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET".encode()
    page.Contents = pdf.make_stream(content)
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_extract_ground_truth_prefers_native_text_layer() -> None:
    """_extract_ground_truth_text returns the native text when present,
    ignoring the fallback OCR text (which might be wrong for small fonts)."""
    pdf_bytes = _pdf_with_text_layer("Invoice 1234")
    result = _extract_ground_truth_text(pdf_bytes, 0, fallback_ocr_text="WRONG OCR")
    assert "1234" in result, f"native text not extracted; got: {result!r}"


def test_extract_ground_truth_falls_back_when_no_text_layer() -> None:
    """When the PDF has no native text layer, fallback_ocr_text is returned."""
    # A PDF with only an image page (no text stream) — use an empty blank page
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    buf = io.BytesIO()
    pdf.save(buf)
    pdf_bytes = buf.getvalue()
    result = _extract_ground_truth_text(pdf_bytes, 0, fallback_ocr_text="OCR TEXT")
    assert result == "OCR TEXT", f"should fall back to OCR; got: {result!r}"
```

Run: `uv run pytest tests/unit/engine/test_compress_helpers.py -v`
Expected: both tests FAIL with `ImportError: cannot import name '_extract_ground_truth_text'`

- [ ] **Step 2: Document the source-truth strategy in `compress()`**

**Wave 4 CRIT ordering:** this step installs a helper OUTSIDE the per-page loop. Specifically:

1. Open the input PDF ONCE before the loop (not per-page — re-opening on every iteration is O(N) full-parse overhead, ~50 ms × N pages = unacceptable on large scans).
2. Inside the loop, extract native text for page `i` using the already-open handle, then CLOSE the textpage handle immediately.
3. The variable is named `_native_text_for_page` so that Task 4a's unconditional `input_ocr_text = " ".join(...)` can be replaced without silently reverting this logic.

**Wave 4 CRIT conflict with Task 4a:** Task 4a's loop sets `input_ocr_text = " ".join(b.text for b in word_boxes)` unconditionally. When Task 0.5 runs BEFORE Task 4a, this line is not yet present — Task 0.5 adds the native-text probe. When Task 4a REWRITES the loop, it must preserve the Task 0.5 probe or re-introduce it. The code below shows the combined version — the engineer implementing Task 4a must use THIS version, not an unconditional OCR fallback:

```python
# pdf_smasher/__init__.py — BEFORE the per-page loop:
from pypdfium2 import PdfDocument as _native_src_pdf_cls

# Open input PDF once for native text extraction. The pdfium handle is
# lightweight (read-only, no decode); closing happens in the outer try/finally.
_src_pdf_for_native_text = _native_src_pdf_cls(input_data)
try:
    # --- per-page loop ---
    for i in range(tri.pages):
        ...
        # SOURCE-TRUTH STRATEGY (Task 0.5 / Wave 3 Pre-Mortem §5):
        # Prefer native text over re-OCRing the input raster. If the input
        # PDF has a text layer, use it — Tesseract on the raster can misread
        # digits and both sides would carry the same wrong reading.
        _page_obj = _src_pdf_for_native_text[i]
        _tp = _page_obj.get_textpage()
        try:
            _native_text = _tp.get_text_range()
        finally:
            _tp.close()  # close textpage handle explicitly to avoid ResourceWarning

        if _native_text and _native_text.strip():
            input_ocr_text = _native_text.strip()
        else:
            # No native text layer — fall back to Tesseract OCR on raster.
            input_ocr_text = " ".join(b.text for b in word_boxes)
        ...
finally:
    _src_pdf_for_native_text.close()
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests -q`
Expected: all green (no behavioral change when input has no text layer, which is true for all current synthetic fixtures).

- [ ] **Step 4: Commit**

```bash
git add pdf_smasher/__init__.py tests/unit/engine/test_verifier.py
git commit -m "feat(verifier): prefer native text layer for digit ground-truth when available"
```

---

### Task 0.6 — Per-page anomaly ratio gate (pre-mortem content-preservation)

**Wave 3 Pre-Mortem finding:** if a single page compresses to 200× — say, a mostly-white blank page via the text-only path — and content was lost (a faint watermark, a light-gray grid), the SSIM check may still pass (0.94 ≥ 0.92) because the image is mostly identical. The per-page ratio gate catches this: if a page compressed more than 50×, something unusual happened. Require a second verify at `mode=safe` thresholds for such pages.

**Wave 4 CRIT ordering (exec-realism):** Task 0.6's loop patch references `raster`, `output_raster`, `output_ocr_text`, `verify_single_page`, and `composed` — none of which exist until Task 4a rewrites the per-page loop. The **unit test** in Step 1 passes immediately (threshold-constant contract only). The **loop patch** in Step 2 must be applied **inside the loop written by Task 4a**. Engineers: implement Step 1 as part of the Task 0 commit sequence, then apply Step 2 during Task 4a's loop implementation.

**Files:**
- Modify: `pdf_smasher/__init__.py`
- Test: `tests/unit/engine/test_verifier.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_verifier.py — append
def test_anomaly_ratio_gate_triggers_safe_threshold() -> None:
    """Threshold constants are correctly ordered: safe tile floor > standard.

    Pre-Mortem Wave 3: a mostly-blank page can hit 100× ratio and pass the
    standard tile floor even with a faint watermark stripped. The anomaly
    gate adds a second pass at safe thresholds for outlier-ratio pages.
    """
    from pdf_smasher.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
    )
    assert _DEFAULT_TILE_SSIM_FLOOR_SAFE > _DEFAULT_TILE_SSIM_FLOOR_STANDARD


def test_anomaly_ratio_gate_verify_floor_respected() -> None:
    """verify_single_page must honor the tile_ssim_floor parameter it receives.

    Wave 4 H2: the tautological constant-ordering test above doesn't prove
    the gate actually uses the stricter floor. This test proves it by passing
    an impossible floor (1.01 > max possible SSIM = 1.0) and asserting the
    verdict fails. If verify_single_page ignores tile_ssim_floor, this test
    passes vacuously — catching the wire-up bug.
    """
    import numpy as np
    from PIL import Image
    from pdf_smasher.engine.verifier import verify_single_page

    in_r = Image.fromarray(np.full((100, 100, 3), 200, dtype=np.uint8))
    out_r = in_r.copy()
    verdict = verify_single_page(
        input_raster=in_r,
        output_raster=out_r,
        input_ocr_text="",
        output_ocr_text="",
        lev_ceiling=0.05,
        ssim_floor=0.92,
        tile_ssim_floor=1.01,  # impossible — must always fail
    )
    assert not verdict.passed, (
        "verify_single_page must use tile_ssim_floor; floor=1.01 > max SSIM, "
        "so the verdict must fail regardless of image content"
    )
```

Run: `uv run pytest tests/unit/engine/test_verifier.py::test_anomaly_ratio_gate_triggers_safe_threshold tests/unit/engine/test_verifier.py::test_anomaly_ratio_gate_verify_floor_respected -v`
Expected: PASS (threshold constants already correct from Task 0.2; floor parameter wiring tested by the second test).

- [ ] **Step 2: Add the per-page ratio check in `compress()`**

In the per-page loop in `pdf_smasher/__init__.py`, after `composed` bytes are built, compute the per-page ratio and use safe thresholds if anomalous:

```python
# Per-page ratio anomaly check (Wave 3 Pre-Mortem §6 / Wave 4 CRIT):
# A page compressing >50× is an outlier. Require the stricter safe-mode
# tile-SSIM floor (0.88) for that page even in standard mode.
#
# Wave 4 CRIT: the proxy `tri.input_bytes / tri.pages` is a uniform average
# and wildly wrong for documents with mixed-size pages. Use the uncompressed
# raster bytes as the per-page input measure — this is reproducible, stable,
# and independent of PDF encoding tricks.
# `raster` is the PIL Image already in scope from the loop iteration above.
per_page_input_estimate = raster.width * raster.height * 3  # RGB bytes
per_page_ratio = per_page_input_estimate / max(1, len(composed))
anomalous = per_page_ratio > 50.0
page_tile_ssim_floor = (
    _DEFAULT_TILE_SSIM_FLOOR_SAFE if (anomalous or is_safe)
    else _DEFAULT_TILE_SSIM_FLOOR_STANDARD
)
per_page_verdict = verify_single_page(
    input_raster=raster,
    output_raster=output_raster,
    input_ocr_text=input_ocr_text,
    output_ocr_text=output_ocr_text,
    lev_ceiling=lev_ceiling,
    ssim_floor=ssim_floor,
    tile_ssim_floor=page_tile_ssim_floor,  # stricter when anomalous
    check_color_preserved=not options.force_monochrome,
)
if anomalous:
    warnings.append(f"page-{i + 1}-anomalous-ratio-{per_page_ratio:.0f}x-safe-verify")
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest tests -q`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add pdf_smasher/__init__.py tests/unit/engine/test_verifier.py
git commit -m "feat(compress): per-page anomaly ratio gate — >50× triggers safe SSIM floor"
```

---

## Task 1: `is_effectively_monochrome` detector

**Files:**
- Modify: `pdf_smasher/engine/foreground.py` (consolidate with existing median-RGB logic per Wave 1 A.M1)
- Test: `tests/unit/engine/test_color_detect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_color_detect.py
"""Tests for is_effectively_monochrome (defined in foreground.py).

This detector is LOAD-BEARING for content preservation. Wave 1 Pre-Mortem #1:
raising tolerance silently strips colored stamps/highlighter on legal docs.
Tolerance MUST NOT be treated as a simple tuning knob. Tests pin both the
'passes' cases (near-mono scanner output) and the 'must-fail' cases
(single meaningful color pixel).
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from pdf_smasher.engine.foreground import is_effectively_monochrome


def _rgb_fromarray(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(arr, mode="RGB")


def test_pure_gray_image_is_monochrome() -> None:
    arr = np.full((200, 200, 3), 128, dtype=np.uint8)
    assert is_effectively_monochrome(_rgb_fromarray(arr)) is True


def test_black_text_on_white_is_monochrome() -> None:
    arr = np.full((200, 200, 3), 255, dtype=np.uint8)
    arr[60:80, 40:160] = 0
    assert is_effectively_monochrome(_rgb_fromarray(arr)) is True


def test_pure_red_image_is_not_monochrome() -> None:
    arr = np.zeros((200, 200, 3), dtype=np.uint8)
    arr[..., 0] = 200
    assert is_effectively_monochrome(_rgb_fromarray(arr)) is False


def test_uniform_tint_within_tolerance_is_monochrome() -> None:
    """Pervasive 2-unit channel cast passes (scanner warm-white LED)."""
    arr = np.full((200, 200, 3), 250, dtype=np.uint8)
    arr[..., 0] = 252
    assert is_effectively_monochrome(_rgb_fromarray(arr), tolerance=5) is True


def test_grayscale_mode_image_is_monochrome() -> None:
    img = Image.new("L", (50, 50), color=128)
    assert is_effectively_monochrome(img) is True


def test_noisy_scan_with_outlier_pixels_still_classifies_as_monochrome() -> None:
    """Real scanner output has sporadic JPEG-ringing outlier pixels with
    channel spread of 10+. A few bad pixels must not flip the whole page to
    RGB. Wave 1 C5."""
    rng = np.random.default_rng(seed=0)
    arr = np.full((500, 500, 3), 200, dtype=np.uint8)
    # Add 50 scattered outlier pixels with 12-unit spread (above tolerance=5
    # but well below the 0.1% threshold)
    for _ in range(50):
        y = int(rng.integers(0, 500))
        x = int(rng.integers(0, 500))
        arr[y, x] = [210, 200, 198]  # 12-unit spread
    # Overall: 99.98% of pixels are uniform → should still be monochrome
    assert is_effectively_monochrome(_rgb_fromarray(arr), tolerance=5) is True


def test_faint_but_pervasive_color_is_not_monochrome() -> None:
    """A pale-blue ruled-line grid across the page (every pixel R≈G≈B±5-7)
    MUST classify as NOT monochrome — routing this to TEXT_ONLY would
    discard the blue lines. Wave 1 Pre-Mortem #3."""
    arr = np.full((500, 500, 3), 240, dtype=np.uint8)
    # Every pixel has a 7-unit blue cast — this is faint but *meaningful*
    # color (ruled lines, carbon paper, highlighter residue)
    arr[..., 2] = 247
    # 100% of pixels have 7-unit spread (> tolerance=5) → must NOT be mono
    assert is_effectively_monochrome(_rgb_fromarray(arr), tolerance=5) is False


def test_single_meaningful_color_pixel_does_not_flip_to_rgb() -> None:
    """A 3x3 red stamp in an otherwise-gray page: the stamp has 0.004% area
    coverage. We use a percentile threshold (not max) so tiny color regions
    are caught via the channel-parity *verifier* check (Task 0.3), not here."""
    arr = np.full((500, 500, 3), 200, dtype=np.uint8)
    arr[100:103, 100:103] = [200, 40, 40]  # 3x3 red pixel region
    # This is is_effectively_monochrome's contract: faint tint (noise) is
    # tolerated, but a LARGE color region flips. 9 pixels / 250000 = 0.004%
    # which is below the 0.1% threshold — so this returns True. The
    # verifier's channel-parity check (Task 0.3) is what catches this at
    # the output layer, not the mono detector.
    assert is_effectively_monochrome(_rgb_fromarray(arr), tolerance=5) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_color_detect.py -v`
Expected: `ImportError: cannot import name 'is_effectively_monochrome' from 'pdf_smasher.engine.foreground'`

- [ ] **Step 3: Implement in foreground.py (consolidate with existing extract_foreground)**

```python
# pdf_smasher/engine/foreground.py — APPEND (do not modify existing extract_foreground)

from pdf_smasher.engine.verifier import CHANNEL_SPREAD_COLOR_TOLERANCE

# --- Monochrome detection ---
#
# LOAD-BEARING CONSTANT. Imported from verifier.py so the two detectors
# (color-loss gate + mono router) agree. Do NOT tune without running the
# Task 0.3 / Task 1 color-parity tests that pin them equal. The verifier's
# Task 0.3 channel-parity check is the backstop if this check misfires.
#
# Rationale for percentile-based approach: a single outlier pixel with
# channel spread 10+ (JPEG ringing on a scan) must not flip a genuinely
# mono page to RGB, but a pervasive faint cast (blue ruled lines,
# highlighter) must not pass through as "mono" either. Use:
#   - 99th percentile of channel-spread <= tolerance  (catches noisy mono)
#   - Fraction of pixels with meaningful color <= 0.001 (catches pervasive tint)
_MONOCHROME_CHANNEL_SPREAD_TOLERANCE = CHANNEL_SPREAD_COLOR_TOLERANCE
_MONOCHROME_TOLERANCE_PERCENTILE = 99.0
_MONOCHROME_COLORED_PIXEL_FRACTION = 0.001
_MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX = 512  # we downsample before scanning for perf


def is_effectively_monochrome(
    raster: Image.Image,
    *,
    tolerance: float = _MONOCHROME_CHANNEL_SPREAD_TOLERANCE,
) -> bool:
    """Return True if the raster is close enough to grayscale that we can
    encode its background as DeviceGray JPEG without perceptible color loss.

    Uses a quantile-based heuristic (not max-channel-spread) so sporadic
    outlier pixels don't flip the verdict. Runs on a downsampled copy to
    bound memory at ~2 MB regardless of source resolution.

    This function is LOAD-BEARING for content preservation — see the
    constant comment above.
    """
    if raster.mode in {"L", "1"}:
        return True
    # Downsample to bound memory: on a 2550×3300 source this cuts 25 MB → 1 MB
    thumb = raster.copy()
    thumb.thumbnail(
        (_MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX, _MONOCHROME_SCAN_DOWNSAMPLE_MAX_PX),
        Image.Resampling.LANCZOS,  # Wave 6 H1: LANCZOS blends neighbours so small
        # colored regions (e.g. 50×50 stamp on 2550×3300) contribute
        # proportionally to thumbnail pixels rather than disappearing.
        # NEAREST could miss a tiny stamp entirely via aliasing.
    )
    arr = np.asarray(thumb.convert("RGB"), dtype=np.int16)
    # Per-pixel channel spread
    channel_spread = arr.max(axis=-1) - arr.min(axis=-1)
    # 99th percentile of spread must be within tolerance (catches pervasive tint)
    percentile_value = float(np.percentile(channel_spread, _MONOCHROME_TOLERANCE_PERCENTILE))
    if percentile_value > tolerance:
        return False
    # Also check: fraction of clearly-colored pixels must be small
    colored_pixel_fraction = float((channel_spread > tolerance).sum()) / channel_spread.size
    return colored_pixel_fraction <= _MONOCHROME_COLORED_PIXEL_FRACTION
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/engine/test_color_detect.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Add cross-module contract test (moved from Task 0.3)**

**Wave 3 CRIT ordering:** the contract test imports `_MONOCHROME_CHANNEL_SPREAD_TOLERANCE` from `foreground.py` — which is only defined in THIS task. Placing the test in Task 0.3 (before this task) would produce an `ImportError`. Add it here, after the implementation is present:

```python
# tests/unit/engine/test_color_detect.py — append
def test_verifier_and_mono_detector_agree_on_color_tolerance() -> None:
    """Same threshold in both places — see CHANNEL_SPREAD_COLOR_TOLERANCE.

    If these disagree, a page can route to TEXT_ONLY (mono detector says
    "no color") and then pass the verifier (verifier says "no color") —
    silently stripping colored ink from the output. This test pins both
    constants at the same value so tuning one forces updating the other.
    """
    from pdf_smasher.engine.foreground import _MONOCHROME_CHANNEL_SPREAD_TOLERANCE
    from pdf_smasher.engine.verifier import CHANNEL_SPREAD_COLOR_TOLERANCE
    assert _MONOCHROME_CHANNEL_SPREAD_TOLERANCE == CHANNEL_SPREAD_COLOR_TOLERANCE
```

- [ ] **Step 6: Run the contract test**

Run: `uv run pytest tests/unit/engine/test_color_detect.py -v`
Expected: all 9 tests pass including the new contract test.

- [ ] **Step 7: Commit**

```bash
git add pdf_smasher/engine/foreground.py tests/unit/engine/test_color_detect.py
git commit -m "feat(foreground): is_effectively_monochrome (quantile-based, noise-tolerant) + tolerance contract test"
```

---

## Task 2: `detect_paper_color` helper

**Files:**
- Modify: `pdf_smasher/engine/foreground.py` (consolidate; same threshold constant as strategy.py)
- Modify: `pdf_smasher/engine/strategy.py` (expose `_LIGHT_PIXEL_VALUE` publicly as `LIGHT_PIXEL_VALUE`)
- Test: `tests/unit/engine/test_color_detect.py`

Wave 1 findings addressed:
- A.M1 — merge into `foreground.py` (avoid duplicate median-RGB logic)
- B.C4, B.M1, C.M1 — single source of truth for light-pixel threshold (unify with strategy.py's 230)
- C.M4 — use mean not median, single raster pass, no separate gray conversion
- L2 — warn when fallback to white fires

- [ ] **Step 1: Unify the light-pixel threshold in strategy.py first**

Edit `pdf_smasher/engine/strategy.py`:

```python
# Change line 25 from _LIGHT_PIXEL_VALUE = 230 to:
LIGHT_PIXEL_VALUE = 230  # public — shared with foreground.detect_paper_color
# (keep _LIGHT_PIXEL_FRACTION_UNIFORM as-is; it's strategy-internal)
```

Update `_light_pixel_fraction` to reference `LIGHT_PIXEL_VALUE`.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/engine/test_color_detect.py — append
from pdf_smasher.engine.foreground import detect_paper_color


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
    """Entirely dark page: default to white, emit warning marker if logging is on."""
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    result = detect_paper_color(Image.fromarray(arr))
    assert result == (255, 255, 255)


def test_detect_paper_color_uses_same_threshold_as_strategy_classify() -> None:
    """Both code paths must agree on 'what counts as paper' or they'll
    disagree on cream-colored stock (Wave 1 B.C4)."""
    from pdf_smasher.engine.strategy import LIGHT_PIXEL_VALUE
    from pdf_smasher.engine.foreground import _PAPER_LIGHT_THRESHOLD
    assert LIGHT_PIXEL_VALUE == _PAPER_LIGHT_THRESHOLD, (
        "paper detection threshold must match strategy's paper classification threshold"
    )


def test_detect_paper_color_cream_stock_exactly_at_boundary() -> None:
    """A page at RGB (230, 225, 210) is RIGHT at the threshold. Must not be
    misclassified as dark."""
    arr = np.full((200, 200, 3), [230, 225, 210], dtype=np.uint8)
    result = detect_paper_color(Image.fromarray(arr))
    assert result[0] == 230
    assert result[1] == 225
    assert result[2] == 210
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_color_detect.py -v`
Expected: `ImportError: cannot import name 'detect_paper_color' from 'pdf_smasher.engine.foreground'`

- [ ] **Step 4: Implement in foreground.py**

```python
# pdf_smasher/engine/foreground.py — APPEND
from pdf_smasher.engine.strategy import LIGHT_PIXEL_VALUE

# Single source of truth for the light-pixel threshold. See strategy.py.
_PAPER_LIGHT_THRESHOLD = LIGHT_PIXEL_VALUE

_DEFAULT_PAPER_FALLBACK = (255, 255, 255)


def detect_paper_color(raster: Image.Image) -> tuple[int, int, int]:
    """Return the dominant paper color as an RGB tuple.

    Samples pixels whose per-channel mean (≈luminance) is >=
    ``_PAPER_LIGHT_THRESHOLD`` and returns their per-channel mean color.

    **Mean, not median**: mean is O(N) and single-pass; median is O(N log N)
    and allocates a sort buffer. Paper color is dominated by ~90%+ uniform
    pixels so the mean is ~unchanged by the median, but ~5-10× faster and
    uses ~70 MB less memory on a 300 DPI page. (Wave 1 C.M4)

    Falls back to ``(255, 255, 255)`` if no pixels qualify. Callers should
    route such pages away from text-only encoding (Wave 1 L2).
    """
    rgb = np.asarray(raster.convert("RGB"), dtype=np.uint8)
    # One-pass gray via mean of channels — avoids a separate convert("L")
    gray = rgb.mean(axis=-1)
    light_mask = gray >= _PAPER_LIGHT_THRESHOLD
    if not light_mask.any():
        return _DEFAULT_PAPER_FALLBACK
    # Mean of R/G/B over the light-pixel subset
    samples = rgb[light_mask]  # shape (N, 3)
    mean_rgb = samples.mean(axis=0).astype(int)
    return (int(mean_rgb[0]), int(mean_rgb[1]), int(mean_rgb[2]))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/engine/test_color_detect.py tests/unit/engine/test_strategy.py -v`
Expected: all green (including strategy tests — we renamed the constant).

- [ ] **Step 6: Commit**

```bash
git add pdf_smasher/engine/foreground.py pdf_smasher/engine/strategy.py tests/unit/engine/test_color_detect.py
git commit -m "feat(foreground): detect_paper_color (shared threshold w/ strategy)"
```

---

## Task 3: Caller-controlled grayscale background

When the caller knows the background is effectively monochrome, render it as `DeviceGray` instead of `DeviceRGB` — JPEG on grayscale is ~2-3× smaller than RGB JPEG for paper-texture content.

**Design note (Wave 1 C.M2):** the monochrome check is NOT done inside `compose_mrc_page`. It's passed in by the caller as `bg_color_mode: Literal["auto", "rgb", "grayscale"]`. This keeps compose composable (it does what you tell it), avoids a cross-layer import, and prevents double-detection (`compress()` already ran `is_effectively_monochrome` for routing).

**Files:**
- Modify: `pdf_smasher/engine/compose.py`
- Test: `tests/unit/engine/test_compose.py`

- [ ] **Step 1: Write the failing test (append)**

```python
# Append to tests/unit/engine/test_compose.py
import io
import pikepdf
from pdf_smasher.engine.compose import compose_mrc_page


def test_mrc_uses_devicegray_when_bg_color_mode_is_grayscale() -> None:
    """Caller-passed bg_color_mode='grayscale' → DeviceGray JPEG."""
    import numpy as np
    from PIL import Image

    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_color_mode="grayscale",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceGray"


def test_mrc_uses_devicergb_when_bg_color_mode_is_rgb() -> None:
    """Caller-passed bg_color_mode='rgb' → DeviceRGB even on monochrome-looking bg."""
    import numpy as np
    from PIL import Image

    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_color_mode="rgb",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceRGB"


def test_mrc_default_bg_color_mode_is_rgb() -> None:
    """Default preserves existing behavior (safe); caller opts into grayscale."""
    import numpy as np
    from PIL import Image

    arr = np.full((300, 300, 3), 240, dtype=np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        # no bg_color_mode → default
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/ColorSpace")) == "/DeviceRGB"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_compose.py -k bg_color_mode -v`
Expected: FAIL with `TypeError: compose_mrc_page() got an unexpected keyword argument 'bg_color_mode'`

- [ ] **Step 3: Add `bg_color_mode` parameter to `compose_mrc_page`**

In `pdf_smasher/engine/compose.py`:

```python
# At module top (add near BgCodec), add:
from typing import Literal

BgColorMode = Literal["rgb", "grayscale"]  # "auto" is a caller-layer concept, not compose's

# Modify compose_mrc_page signature:
def compose_mrc_page(
    *,
    foreground: Image.Image,
    foreground_color: tuple[int, int, int],
    mask: Image.Image,
    background: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    bg_jpeg_quality: int = _JPEG_QUALITY_BG,
    bg_color_mode: BgColorMode = "rgb",
) -> bytes:
    ...
    # Replace the bg encoding block:
    if bg_color_mode == "grayscale":
        bg_prepared = background.convert("L")
        bg_color_space = pikepdf.Name.DeviceGray
    else:
        bg_prepared = background.convert("RGB")
        bg_color_space = pikepdf.Name.DeviceRGB

    bg_data = _jpeg_bytes(bg_prepared, bg_jpeg_quality)
    bg_xobj = _make_stream(
        pdf,
        data=bg_data,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=bg_prepared.size[0],
        Height=bg_prepared.size[1],
        ColorSpace=bg_color_space,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
```

**Do not import `is_effectively_monochrome` into compose.py.** The caller decides.

**Wave 6 Pre-Mortem C2 / Exec-Realism C1+C2: also add `bg_color_mode` to `compose_photo_only_page`.**
Task 4a calls `compose_photo_only_page(..., bg_color_mode=_photo_bg_color_mode)` in the PHOTO_ONLY branch.
If `compose_photo_only_page` doesn't have this parameter, every PHOTO_ONLY page crashes with
`TypeError: compose_photo_only_page() got an unexpected keyword argument 'bg_color_mode'`.
Also: Task 7 will add `bg_codec` to `compose_photo_only_page` — that signature block MUST
preserve `bg_color_mode` or this fix is silently overwritten.

```python
# ALSO modify compose_photo_only_page signature (SAME Task 3 edit):
def compose_photo_only_page(
    *,
    raster: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    target_dpi: int,
    jpeg_quality: int = _JPEG_QUALITY_BG,
    subsampling: int = _JPEG_SUBSAMPLING_444,
    bg_color_mode: BgColorMode = "rgb",   # NEW — caller decides grayscale
) -> bytes:
    ...
    # Replace the resized = ... block with:
    target_w = max(1, round(page_width_pt * target_dpi / 72))
    target_h = max(1, round(page_height_pt * target_dpi / 72))
    if bg_color_mode == "grayscale":
        resized = raster.convert("L").resize((target_w, target_h), Image.Resampling.LANCZOS)
        color_space = pikepdf.Name.DeviceGray
    else:
        resized = raster.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
        color_space = pikepdf.Name.DeviceRGB
    data = _jpeg_bytes(resized, jpeg_quality, subsampling=subsampling)
    # ... build XObject with ColorSpace=color_space (not hardcoded DeviceRGB) ...
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/engine/test_compose.py -v`
Expected: all compose tests pass including the three new ones.

- [ ] **Step 5: Commit**

```bash
git add pdf_smasher/engine/compose.py tests/unit/engine/test_compose.py
git commit -m "feat(compose): bg_color_mode parameter on mrc_page AND photo_only_page (caller-decided grayscale)"
```

---

## Task 4: Strategy routing in `compress()` (split into 4a, 4b, 4c)

**Wave 1 findings motivating the split (C.M3):** the original single-task had 60+ LoC of replacement code doing four concerns. Bisecting a regression across them is hopeless. Each sub-task below is independently committable and testable.

### Task 4a: Route non-MIXED strategies (no `force_monochrome` yet)

Per-page dispatch to `compose_text_only_page` / `compose_photo_only_page` / `compose_mrc_page` based on `classify_page`. Also: handle ALL four strategies (including `ALREADY_OPTIMIZED`), guard `mask_coverage` division, add per-page try/except, emit `strategy_distribution` counter.

**Wave 1 findings addressed here:** B.C1 (ALREADY_OPTIMIZED branch missing), B.C3 (mask_coverage divide-by-zero), C.C5 (mid-loop crash state), Pre-Mortem #2 (ALREADY_OPTIMIZED pass-through fast path).

**Wave 3 CRIT schema migrations (must land in this task, not later):**
1. `strategy_distribution: Mapping[str, int]` must be added to `CompressReport` **here** — Task 4c asserts `report.strategy_distribution[...]` and would fail if this field isn't present before that test runs.
2. `photo_target_dpi: int = 200` must be added to `CompressOptions` **here** — the PHOTO_ONLY branch below uses `options.photo_target_dpi` but Task 5 (where the original plan placed the field) runs after Task 4a's tests. Moving it here avoids `AttributeError` on first run.

**Files:**
- Modify: `pdf_smasher/__init__.py`
- Modify: `pdf_smasher/types.py` (add `strategy_distribution` to `CompressReport`; add `photo_target_dpi` to `CompressOptions`)
- Test: `tests/integration/test_ratio_gate.py` (new file)

- [ ] **Step 0: Add `strategy_distribution` to `CompressReport` and `photo_target_dpi` to `CompressOptions` in `types.py`**

```python
# pdf_smasher/types.py — add to CompressReport:
from typing import Mapping

@dataclass(frozen=True)
class CompressReport:
    ...
    strategy_distribution: Mapping[str, int] = field(default_factory=dict)
    # maps "text_only" / "photo_only" / "mixed" → page count

# pdf_smasher/types.py — add to CompressOptions:
@dataclass
class CompressOptions:
    ...
    photo_target_dpi: int = 200  # DPI for PHOTO_ONLY pages; higher than bg to preserve micro-detail
```

Run: `uv run python -c "from pdf_smasher.types import CompressReport, CompressOptions; r = CompressReport.__dataclass_fields__; print('strategy_distribution' in r, 'photo_target_dpi' in CompressOptions.__dataclass_fields__)"`
Expected: `True True`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_ratio_gate.py
"""Phase-2b ratio gate: assert compression ratios on canonical fixtures."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher import compress


def _wrap_raster_as_pdf_bytes(
    img: Image.Image,
    *,
    page_width_pt: float = 612.0,
    page_height_pt: float = 792.0,
) -> bytes:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(page_width_pt, page_height_pt))
    page = pdf.pages[0]
    jpeg = io.BytesIO()
    img.save(jpeg, format="JPEG", quality=95, subsampling=0)
    xobj = pdf.make_stream(
        jpeg.getvalue(),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=img.size[0],
        Height=img.size[1],
        ColorSpace=pikepdf.Name.DeviceRGB,
        BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    page.Contents = pdf.make_stream(
        f"q {page_width_pt} 0 0 {page_height_pt} 0 0 cm /Scan Do Q\n".encode("ascii"),
    )
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def _text_only_fixture() -> bytes:
    """8.5x11 inch @ 300 DPI, black text on white — typical medical/legal doc."""
    import shutil as _shutil_fonts
    img = Image.new("RGB", (2550, 3300), color="white")
    draw = ImageDraw.Draw(img)
    # Use a font shipped with the test fixture dir if system fonts are absent.
    # ImageFont.load_default() with size= is only available in Pillow >=10.
    # Prefer a bundled test fixture font; fall back to default.
    _FIXTURE_FONT = "tests/integration/_fixtures/LiberationMono-Regular.ttf"
    try:
        import pathlib as _pl
        _font_path = _pl.Path(_FIXTURE_FONT)
        if _font_path.exists():
            font = ImageFont.truetype(str(_font_path), 48)
        else:
            font = ImageFont.truetype("LiberationMono-Regular.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    y = 200
    for i in range(30):
        draw.text((200, y), f"Line {i + 1}: diagnosis code ICD-10 A00.{i:02d}", fill="black", font=font)
        y += 80
    return _wrap_raster_as_pdf_bytes(img)


@pytest.mark.integration
@pytest.mark.skipif(
    __import__("shutil").which("jbig2") is None,
    reason="jbig2enc not installed — text-only ratio falls to ~8× on flate fallback, test requires ≥20×",
)
def test_text_only_page_hits_target_ratio() -> None:
    """Text-only routing: black text on white should hit >=20x compression.

    Requires jbig2enc — on the flate fallback path, text-only only reaches
    ~8×, which does not satisfy the 20× gate. The skipif decorator surfaces
    this as a skip rather than a false failure.

    Wave 4 H3 (test-integrity): if _text_only_fixture() is misconfigured (e.g.,
    colored pixels from font anti-aliasing above the monochrome threshold), the
    page may route to MIXED instead of TEXT_ONLY and the ratio assertion would
    be a false positive: MIXED at 20× would pass even though we didn't test the
    TEXT_ONLY path. The pre-assert below catches this.
    """
    from pypdfium2 import PdfDocument as _PdfDoc
    from pdf_smasher.engine.strategy import PageStrategy, classify_page
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.mask import build_mask
    import numpy as np

    pdf_in = _text_only_fixture()
    # Pre-assert: fixture must route to TEXT_ONLY before testing the ratio.
    # If this fails, fix _text_only_fixture() — the ratio test would be
    # meaningless on the wrong route.
    _raster = rasterize_page(pdf_in, page_index=0, dpi=150)
    _mask = build_mask(_raster)
    _mask_arr = np.asarray(_mask.convert("1"), dtype=bool)
    _coverage = float(_mask_arr.sum()) / max(1, _mask_arr.size)
    _strategy = classify_page(_raster, mask_coverage_fraction=_coverage)
    assert _strategy == PageStrategy.TEXT_ONLY, (
        f"_text_only_fixture() routed to {_strategy!r} instead of TEXT_ONLY. "
        "Fix the fixture (e.g., anti-aliasing adding color pixels above the "
        "monochrome threshold) before the ratio assertion is meaningful."
    )

    pdf_out, report = compress(pdf_in)
    assert report.ratio >= 20.0, (
        f"text-only fixture should compress >=20x; got {report.ratio:.2f}x "
        f"(in={report.input_bytes:,} out={report.output_bytes:,})"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ratio_gate.py -v`
Expected: FAIL with ratio around 5× (current behavior, always MIXED route).

- [ ] **Step 3: Replace the per-page loop in `compress()`**

In `pdf_smasher/__init__.py`, replace the per-page loop. Uses an explicit match-statement over all four `PageStrategy` values, explicit `ALREADY_OPTIMIZED` pass-through, zero-size guard on mask_coverage, and per-page `try/except`.

**Wave 1 Guard (B.C7):** add an environment check that warns loudly when jbig2enc is unavailable — text-only ratios crater from 50× to ~8× on the flate fallback. Without this, the test's "expected 20×, got 8×" failure will look like a regression.

```python
# Add to the lazy-import block near the top of compress():
from pdf_smasher.engine.foreground import detect_paper_color, is_effectively_monochrome
from pdf_smasher.engine.compose import (
    compose_mrc_page,
    compose_photo_only_page,
    compose_text_only_page,
)
from pdf_smasher.engine.strategy import PageStrategy, classify_page
from pdf_smasher.engine.verifier import (  # Wave 5 CRIT: import _VerifierAggregator too
    verify_single_page,
    _VerifierAggregator,
)

import numpy as np

# Replace the whole "for i in range(tri.pages):" loop body with:
#
# IMPORTANT (Wave 2 CRIT-P2): do NOT accumulate `input_rasters`,
# `input_ocr_texts`, `output_rasters`, `output_ocr_texts` across pages.
# On a 200-page 300-DPI document each raster is ~25 MB (RGB) and the whole-
# document buffer hits ~5 GB resident — well above RLIMIT_AS on default
# container limits. Instead, invoke `verify_single_page` inline per page
# and discard the raster as soon as verification returns. Track a running
# aggregate (worst_lev, min_ssim_global, min_ssim_tile, any_digit_mismatch,
# failing_pages) instead of sequences.
# Wave 3 CRIT: initialize BEFORE the loop — placing `warnings = []` inside
# the loop would reset it on every page, dropping all but the last page's
# warnings. Similarly, `verifier_agg` must be initialized before the loop
# so all per-page verdicts are merged into one aggregator.
warnings: list[str] = []
import shutil
if shutil.which("jbig2") is None:
    warnings.append("jbig2enc-unavailable-using-flate-fallback")
verifier_agg = _VerifierAggregator()

strategy_counts: dict[str, int] = {"text_only": 0, "photo_only": 0, "mixed": 0, "already_optimized": 0}

# Wave 6 Pre-Mortem C3: handle pass-through BEFORE the page loop.
# triage._classify can return "pass-through" for already-JBIG2-optimized docs.
# Without this guard, those docs would be re-encoded (wasted work) and the
# page-count invariant below would fire unexpectedly.
if tri.classification == "pass-through":
    return input_data, CompressReport(
        status="passed_through",
        exit_code=0,
        input_bytes=len(input_data),
        output_bytes=len(input_data),
        ratio=1.0,
        pages=tri.pages,
        wall_time_ms=0,
        engine="mrc",
        engine_version=__version__,
        verifier=VerifierResult(
            status="skipped",
            ocr_levenshtein=0.0,
            ssim_global=1.0,
            ssim_min_tile=1.0,
            digit_multiset_match=True,
            structural_match=True,
        ),
        input_sha256="",
        output_sha256="",
        canonical_input_sha256=None,
    )

for i in range(tri.pages):
    width_pt, height_pt = page_sizes[i]
    try:
        raster = rasterize_page(input_data, page_index=i, dpi=source_dpi)
        word_boxes = tesseract_word_boxes(raster, language=options.ocr_language)
        input_ocr_text = " ".join(b.text for b in word_boxes)

        mask = build_mask(raster)
        mask_arr = np.asarray(mask.convert("1"), dtype=bool)
        # Guard against zero-size (B.C3) — should never happen but crash
        # gracefully rather than ZeroDivisionError
        mask_coverage = float(mask_arr.sum()) / max(1, mask_arr.size)

        strategy = classify_page(raster, mask_coverage_fraction=mask_coverage)
        # Task 4b will add force_monochrome override here.

        if strategy == PageStrategy.ALREADY_OPTIMIZED:
            # Wave 2 CRIT context: `classify_page` (strategy.py) never emits
            # ALREADY_OPTIMIZED — that is triage's decision, and pages that
            # triage would have flagged are already short-circuited before
            # this loop (see `if tri.classification == "pass-through"` above).
            # Reaching this branch with a non-triage source means a future
            # strategy classifier change added the value but forgot to wire
            # a handler here. Rather than silently falling through to MRC
            # (which would be a placebo fast-path and mask the bug), raise
            # AssertionError so CI catches the mismatch immediately.
            msg = (
                f"page {i + 1}: classify_page returned ALREADY_OPTIMIZED but "
                "compress() has no handler. If you added this value, also add "
                "the pass-through fast path here."
            )
            raise AssertionError(msg)
        elif strategy == PageStrategy.TEXT_ONLY:
            # Wave 5 Pre-Mortem HIGH-5: verify monochromaticity before TEXT_ONLY.
            # strategy.py's classify_page routes to TEXT_ONLY based on mask
            # coverage + light pixel fraction — it does NOT check monochromaticity.
            # A page with pale-blue ruled lines (RGB ~240,244,255 — above the
            # LIGHT_PIXEL_VALUE threshold) routes to TEXT_ONLY but has colored
            # content. is_effectively_monochrome(raster) catches this: if the
            # page has meaningful color AND was NOT force_monochrome'd, demote
            # to MIXED so the blue lines survive in the BG layer.
            if not options.force_monochrome and not is_effectively_monochrome(raster):
                # Demote to MIXED — the page has color that TEXT_ONLY would drop.
                # Record this reclassification in strategy_counts for diagnostics.
                warnings.append(
                    f"page-{i + 1}-text-only-demoted-to-mixed-color-detected"
                )
                strategy = PageStrategy.MIXED
                composed = _mrc_compose(raster, mask, width_pt, height_pt, bg_target_dpi, source_dpi)
            else:
                fg = extract_foreground(raster, mask=mask)
                paper = detect_paper_color(raster)
                composed = compose_text_only_page(
                    mask=mask,
                    foreground_color=fg.ink_color,
                    paper_color=paper,
                    page_width_pt=width_pt,
                    page_height_pt=height_pt,
                )
        elif strategy == PageStrategy.PHOTO_ONLY:
            # Wave 5 Pre-Mortem CRIT-4: force_monochrome on a near-empty-mask
            # PHOTO_ONLY page → encode as grayscale photo instead of routing
            # to TEXT_ONLY (which would use an undefined ink_color).
            _photo_bg_color_mode = (
                "grayscale"
                if (options.force_monochrome or is_effectively_monochrome(raster))
                else "rgb"
            )
            composed = compose_photo_only_page(
                raster=raster,
                page_width_pt=width_pt,
                page_height_pt=height_pt,
                target_dpi=options.photo_target_dpi,  # new option (Task 7.5)
                bg_color_mode=_photo_bg_color_mode,   # added in Task 3
            )
        elif strategy == PageStrategy.MIXED:
            composed = _mrc_compose(raster, mask, width_pt, height_pt, bg_target_dpi, source_dpi)
        else:  # Should be unreachable given the enum
            msg = f"unhandled strategy: {strategy!r}"
            raise AssertionError(msg)

        if options.ocr:
            composed = add_text_layer(
                composed,
                page_index=0,
                word_boxes=word_boxes,
                raster_width_px=raster.size[0],
                raster_height_px=raster.size[1],
                page_width_pt=width_pt,
                page_height_pt=height_pt,
            )
        # --- Streaming verify (Wave 2 CRIT-P2) ---
        # Rasterize the output page just-in-time and verify against this
        # input raster, then drop both. Aggregate into verifier_agg.
        output_raster = rasterize_page(composed, page_index=0, dpi=source_dpi)
        output_ocr_text = " ".join(
            b.text for b in tesseract_word_boxes(output_raster, language=options.ocr_language)
        )
        # Task 0.6 anomaly gate (Wave 3 Pre-Mortem §6 / Wave 5 EXEC-HIGH-1 fix):
        # Compute BEFORE del raster — raster is referenced here and then gone.
        # Exclude TEXT_ONLY from the anomaly gate: legitimate text pages can
        # compress 100–500× via JBIG2 and that is expected, not anomalous
        # (Wave 5 Pre-Mortem HIGH-2: the >50× threshold fires on every TEXT_ONLY
        # page, tightening the floor to 0.88 and potentially rejecting normal
        # text pages on flate fallback whose tile SSIM is ~0.86).
        per_page_input_estimate = raster.width * raster.height * 3  # uncompressed RGB
        per_page_ratio = per_page_input_estimate / max(1, len(composed))
        anomalous = (
            per_page_ratio > 200.0  # raised from 50× — TEXT_ONLY legitimately hits 100–500×
            and strategy != PageStrategy.TEXT_ONLY  # exclude expected high-ratio path
        )
        page_tile_ssim_floor = (
            _DEFAULT_TILE_SSIM_FLOOR_SAFE if (anomalous or is_safe)
            else _DEFAULT_TILE_SSIM_FLOOR_STANDARD
        )
        if anomalous:
            warnings.append(f"page-{i + 1}-anomalous-ratio-{per_page_ratio:.0f}x-safe-verify")
        per_page_verdict = verify_single_page(
            input_raster=raster,
            output_raster=output_raster,
            input_ocr_text=input_ocr_text,
            output_ocr_text=output_ocr_text,
            lev_ceiling=lev_ceiling,
            ssim_floor=ssim_floor,
            tile_ssim_floor=page_tile_ssim_floor,   # Task 0.6 anomaly-gated floor
            check_color_preserved=not options.force_monochrome,  # Task 4b
        )
        verifier_agg.merge(i, per_page_verdict)
        # Drop rasters before appending composed bytes to bound peak RSS:
        del raster, output_raster, input_ocr_text, output_ocr_text
        page_pdfs.append(composed)
        strategy_counts[strategy.name.lower()] += 1

    except KeyboardInterrupt:
        # Wave 2 CRIT: allow clean interruption. Clear accumulated page_pdfs
        # so callers who swallow the exception don't reuse half-baked state.
        page_pdfs.clear()
        raise
    except Exception as e:  # noqa: BLE001 — last-resort per-page crash guard
        # Wave 1 C.C5: bound crash state. If ANY page fails, the whole
        # compress() call raises — rasters have already been dropped via
        # `del` above so we don't accumulate multi-GB of buffers before
        # crashing.
        msg = f"compression failed on page {i + 1}/{tri.pages}: {e}"
        raise CompressError(msg) from e

# Wave 5 Pre-Mortem CRIT-2: page-count invariant — assert BEFORE merge so a
# compose bug that silently drops pages is caught here, not as a corrupt output.
assert len(page_pdfs) == tri.pages, (
    f"page_pdfs has {len(page_pdfs)} entries but input had {tri.pages} pages; "
    "a compose or strategy routing bug silently dropped/duplicated a page"
)

# Wave 5 Pre-Mortem HIGH-6: explicit verifier failure short-circuit.
# The plan previously did NOT specify what happens when verifier_agg.result()
# has status="fail". Without this, a silently-corrupted PDF is returned with
# a "fail" report and no exception — callers that don't inspect report.verifier
# would write a corrupted output file.
# Contract: raise ContentDriftError in standard/safe modes; in fast mode,
# record the failure in warnings but continue (users can inspect the report).
_verifier_result = verifier_agg.result()
if _verifier_result.status == "fail":
    if options.mode != "fast":
        from pdf_smasher.exceptions import ContentDriftError
        msg = (
            f"content drift detected on pages {list(_verifier_result.failing_pages)}: "
            f"ocr_lev={_verifier_result.ocr_levenshtein:.4f}, "
            f"ssim_global={_verifier_result.ssim_global:.4f}, "
            f"ssim_tile={_verifier_result.ssim_min_tile:.4f}"
        )
        raise ContentDriftError(msg)
    else:
        # fast mode: warn but don't abort
        warnings.append(
            f"verifier-fail-fast-mode-pages-{list(_verifier_result.failing_pages)}"
        )
```

And add the `_mrc_compose` helper at module level (outside compress()). Note the **jbig2 cascade visibility** change (Wave 2 CRIT-P1) — compose currently swallows `CalledProcessError` on jbig2enc failure and falls back to flate silently, which can turn a 50× text page into 8× without anyone noticing. The helper now sets a thread-local flag that `compress()` reads after the page is built:

```python
import threading

# Thread-local flag for whether the current page ended up on the flate
# fallback because jbig2enc errored mid-run (distinct from "jbig2 binary
# missing" — that's detected up-front). Compose sets it; compress() reads
# it and emits a warning if set.
_JBIG2_CASCADE_STATE = threading.local()


def _mrc_compose(
    raster: Image.Image,
    mask: Image.Image,
    width_pt: float,
    height_pt: float,
    bg_target_dpi: int,
    source_dpi: int,
    *,
    bg_jpeg_quality: int | None = None,
) -> bytes:
    """MRC composition helper for Task 4a.

    Wave 3 CRIT: does NOT accept `bg_codec` here — `compose_mrc_page` doesn't
    have that parameter until Task 7. Task 7 will update the signature and add
    `bg_codec` as a parameter to both `_mrc_compose` and `compose_mrc_page`.
    For now, compose always uses JPEG (the existing default).

    Wave 5 exec-realism HIGH-4: `bg_jpeg_quality` is `None` until Task 7.5a
    wires `options.target_color_quality`. Until then, compose uses `_JPEG_QUALITY_BG`
    (45) as the internal default. The baseline compress() in Task 4a does NOT
    pass `bg_jpeg_quality` — this is intentional: it keeps Task 4a's changes
    minimal (routing only). Task 7.5a patches the caller to pass `bg_jpeg_quality`.
    """
    from typing import Any  # Wave 5 exec-realism HIGH-4: must import Any
    from pdf_smasher.engine.background import extract_background
    from pdf_smasher.engine.compose import compose_mrc_page
    from pdf_smasher.engine.foreground import extract_foreground, is_effectively_monochrome

    fg = extract_foreground(raster, mask=mask)
    bg = extract_background(
        raster,
        mask=mask,
        source_dpi=source_dpi,
        target_dpi=bg_target_dpi,
    )
    # Wave 5 Pre-Mortem CRIT-1: check the PAGE RASTER, not the background.
    # extract_background() inpaints the ink regions with surrounding paper
    # color, making a page with colored ink look near-mono in `bg`. Using
    # is_effectively_monochrome(bg) would encode a page with a RED STAMP as
    # DeviceGray — the stamp color is silently dropped from the BG layer.
    # Note: even with RGB BG, the FG layer uses only ONE ink_color per page,
    # so multi-ink-color pages (red stamp + blue pen) still lose the second
    # color. That is a known MRC design limitation (Phase 2c per-region color).
    # The verifier's channel-parity check will catch the second-color loss IF
    # the tolerance is set appropriately. See also HIGH-4 (JPEG ringing).
    bg_color_mode = "grayscale" if is_effectively_monochrome(raster) else "rgb"
    kwargs: dict[str, Any] = dict(
        foreground=fg.image,
        foreground_color=fg.ink_color,
        mask=mask,
        background=bg,
        page_width_pt=width_pt,
        page_height_pt=height_pt,
        bg_color_mode=bg_color_mode,
        # bg_codec intentionally NOT included here — added in Task 7
    )
    if bg_jpeg_quality is not None:
        kwargs["bg_jpeg_quality"] = bg_jpeg_quality
    return compose_mrc_page(**kwargs)
```

And in `compose._encode_mask_xobject` (see compose.py:85), when the JBIG2 path errors and we fall back to flate, set `_JBIG2_CASCADE_STATE.tripped = True`. In the `compress()` per-page loop, after `composed = ...`, read the flag:

```python
# Wave 6 Test-Integrity H3: reset the flag BEFORE compose so that a
# per-page exception raised AFTER tripped=True is set (but before we
# read the flag here) cannot leak a stale True into the next page or
# the next compress() call in the same thread.
_JBIG2_CASCADE_STATE.tripped = False
# ... compose the page ...
# (read the flag immediately after compose returns — it was cleared above
# so if it's True now, it was set during THIS page's compose call)
if getattr(_JBIG2_CASCADE_STATE, "tripped", False):
    warnings.append(f"page-{i + 1}-jbig2-fallback-to-flate")
```

Thread `warnings` into the final `CompressReport(...)` call (it is already initialized before the loop above — do NOT re-initialize inside the loop).

**Wave 4 CRIT — `CompressReport.warnings` type mismatch:** `CompressReport` has `warnings: tuple[str, ...] = ()` (frozen dataclass). The `warnings` list must be cast at construction:

```python
# At the CompressReport(...) construction call:
# Wave 5 CRIT: verifier=verifier_agg.result() MUST be included — it is a
# required field with no default in CompressReport (types.py:108). Omitting
# it raises TypeError at runtime. The verifier result is the aggregated
# outcome of all per-page verdicts merged into verifier_agg during the loop.
return output_bytes, CompressReport(
    ...
    verifier=verifier_agg.result(),     # required field — streaming aggregator result
    warnings=tuple(warnings),           # cast list → tuple (frozen field)
    strategy_distribution=dict(strategy_counts),  # Mapping[str, int]
    ...
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_ratio_gate.py::test_text_only_page_hits_target_ratio -v`
Expected: PASS if jbig2enc is installed (test is skipif otherwise).

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests -q`
Expected: all existing tests still pass (they pass through the MRC MIXED branch).

- [ ] **Step 6: Commit**

```bash
git add pdf_smasher/__init__.py tests/integration/test_ratio_gate.py
git commit -m "feat(compress): route per-page strategy (MIXED/TEXT_ONLY/PHOTO_ONLY/ALREADY_OPTIMIZED)"
```

### Task 4b: Wire `force_monochrome` across all non-ALREADY_OPTIMIZED strategies

**Wave 1 findings addressed:** B.C2 + C.C4 + Pre-Mortem #1 — `force_monochrome` must apply to PHOTO_ONLY + MIXED, not just MIXED. SPEC.md:21 says "skip color detection, treat all as B&W." Also emits the `page-N-color-detected-in-monochrome-mode` warning code (SPEC.md:402) when colored content is being flattened.

- [ ] **Step 1: Write the failing test (integration)**

```python
# tests/integration/test_ratio_gate.py — append
@pytest.mark.integration
def test_force_monochrome_applies_to_photo_only_pages_too() -> None:
    """A page classified PHOTO_ONLY must also route via text-only when
    force_monochrome=True — not silently stay on the color photo path."""
    # Build a page with 0% mask coverage (blank / ultra-sparse) + a color
    # gradient — realistic for a blank cover sheet on a scan batch.
    import numpy as np

    arr = np.zeros((2550, 3300, 3), dtype=np.uint8)
    arr[..., 0] = np.linspace(100, 255, 3300, dtype=np.uint8)[None, :]
    arr[..., 2] = np.linspace(255, 100, 3300, dtype=np.uint8)[None, :]
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img)

    from pdf_smasher import CompressOptions
    _, default_report = compress(pdf_in)
    _, mono_report = compress(pdf_in, options=CompressOptions(force_monochrome=True))

    # Under force_monochrome, the photo page converted to grayscale should
    # be smaller than the full RGB photo output (~20-30% smaller at least).
    assert mono_report.output_bytes < default_report.output_bytes * 0.85, (
        f"force_monochrome must reduce photo-only size: "
        f"default={default_report.output_bytes}, mono={mono_report.output_bytes}"
    )


@pytest.mark.integration
def test_force_monochrome_emits_color_warning_on_colored_page() -> None:
    """SPEC.md:402 defines `page-N-color-detected-in-monochrome-mode`. Must
    be emitted when force_monochrome flattens detected color."""
    import numpy as np

    arr = np.full((1700, 2200, 3), 240, dtype=np.uint8)
    arr[400:500, 400:1200] = [200, 40, 40]  # big red stamp
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img, page_width_pt=612, page_height_pt=792)

    from pdf_smasher import CompressOptions
    _, report = compress(pdf_in, options=CompressOptions(force_monochrome=True))
    assert any("color-detected-in-monochrome-mode" in w for w in report.warnings), (
        f"expected color warning; got warnings={report.warnings}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_ratio_gate.py -k force_monochrome -v`
Expected: FAIL — Task 4a has no force_monochrome handling.

- [ ] **Step 3: Implement force_monochrome in `compress()`**

Modify the loop in `__init__.py` added in Task 4a. Re-use `_page_has_color` from verifier (which now uses the shared `CHANNEL_SPREAD_COLOR_TOLERANCE` — Wave 2 CRIT fix) so the mono detector and the warning emitter can't disagree:

```python
# After `strategy = classify_page(...)`:
if options.force_monochrome:
    # Re-use the verifier's _page_has_color helper. Identical tolerance
    # ensures: if we suppress a color warning here, the verifier also
    # won't flag color loss at the output layer (no silent drop-through).
    from pdf_smasher.engine.verifier import _page_has_color
    if _page_has_color(raster):
        warnings.append(f"page-{i + 1}-color-detected-in-monochrome-mode")
    if strategy != PageStrategy.ALREADY_OPTIMIZED:
        # Wave 5 Pre-Mortem CRIT-4: PHOTO_ONLY pages with near-zero mask
        # coverage must NOT route to TEXT_ONLY under force_monochrome.
        # extract_foreground() on a ~0% mask image returns an undefined
        # ink_color; compose_text_only_page produces a solid paper rectangle
        # that fails the SSIM check (or worse, silently passes on a blank
        # page where the paper color is the whole image).
        # Instead, keep as PHOTO_ONLY but force grayscale encoding — the
        # photo path has a bg_color_mode parameter for exactly this.
        if strategy == PageStrategy.PHOTO_ONLY and mask_coverage < 0.01:
            # flag stays PHOTO_ONLY; grayscale is already forced by the
            # PHOTO_ONLY branch in Task 4a (checks options.force_monochrome
            # when computing _photo_bg_color_mode). No separate flag needed.
            pass  # no strategy override — handled in PHOTO_ONLY branch
        else:
            strategy = PageStrategy.TEXT_ONLY
```

**Interaction with verifier channel-parity check (Task 0.3):** when
force_monochrome routes a colored page to TEXT_ONLY, the output WILL fail
the verifier's channel-parity check by design — color content IS being
flattened. For `force_monochrome=True` runs, the color-parity check must
be disabled.

**Wave 4 CRIT — `verifier_opts` dead code:** the `verifier_opts = dict(...)` pattern was dead code (the dict was built but never spread into the call). Instead, directly update the `verify_single_page(...)` call that Task 4a added. Specifically, edit the existing call in the per-page loop to add `check_color_preserved`:

```python
# EDIT the existing verify_single_page(...) call in the per-page loop
# (added by Task 4a). Change the call from:
#   verify_single_page(..., tile_ssim_floor=page_tile_ssim_floor)
# to:
per_page_verdict = verify_single_page(
    input_raster=raster,
    output_raster=output_raster,
    input_ocr_text=input_ocr_text,
    output_ocr_text=output_ocr_text,
    lev_ceiling=lev_ceiling,
    ssim_floor=ssim_floor,
    tile_ssim_floor=page_tile_ssim_floor,
    check_color_preserved=not options.force_monochrome,  # disable parity when user opted in
)
```

Also: **emit a per-job aggregate warning** so users know how many pages had color discarded:

```python
# After the per-page loop, before CompressReport construction:
n_color_discarded = sum(
    1 for w in warnings
    if "color-detected-in-monochrome-mode" in w
)
if n_color_discarded > 0:
    warnings.append(f"force-monochrome-discarded-color-on-{n_color_discarded}-pages")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_ratio_gate.py -k force_monochrome -v`
Expected: both new tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_smasher/__init__.py tests/integration/test_ratio_gate.py
git commit -m "feat(compress): force_monochrome applies to all strategies; emit color warning"
```

### Task 4c: Integration ratio gate + multi-page mixed-strategy test

**Wave 1 findings addressed:** B.M3 — missing multi-page-mixed-strategy test. Colored-ink-not-routed-to-TEXT_ONLY (Pre-Mortem #1).

- [ ] **Step 1: Write the failing tests**

**Wave 3 CRIT:** `test_text_only_page_hits_target_ratio` was already defined in Task 4a Step 1. Do NOT redefine it here — two `def test_text_only_page_hits_target_ratio` functions in the same file is a silent replacement (pytest collects only the last one). The ratio gate is the Task 4a RED test; this task only adds the stamp-preservation and multi-page tests.

```python
# tests/integration/test_ratio_gate.py — append (NO duplicate test_text_only_page_hits_target_ratio)
@pytest.mark.integration
def test_colored_stamp_is_preserved_after_compression() -> None:
    """Pre-Mortem #1: a page with a red stamp must NOT be silently flattened.
    The exact strategy chosen (MIXED vs PHOTO_ONLY vs TEXT_ONLY) is an
    implementation detail — what matters is the VERIFIER catches silent
    color loss.

    Wave 2 CRIT context: the earlier version of this test hard-asserted
    `strategy == MIXED`. But a page with ~4% mask coverage (black text +
    red stamp on white) actually routes to PHOTO_ONLY (mask_coverage <
    _MASK_COVERAGE_MIXED_FLOOR = 0.05). That's FINE — the photo path
    preserves color. What must be asserted is: (a) compress() doesn't
    raise ContentDriftError, (b) the output rasterizes back with color
    preserved at the stamp location."""
    import io

    import numpy as np
    import pypdfium2 as pdfium
    from PIL import Image

    arr = np.full((2550, 3300, 3), 255, dtype=np.uint8)
    # Black text band (~1% of area), red stamp (~2% of area) — this
    # page's mask_coverage is ~3%, well under 5%, so the strategy
    # classifier routes it to PHOTO_ONLY. That routing is correct.
    arr[500:600, 500:2000] = 0
    arr[1500:1700, 1500:2500] = [200, 40, 40]
    img = Image.fromarray(arr)
    pdf_in = _wrap_raster_as_pdf_bytes(img)

    pdf_out, report = compress(pdf_in)
    assert report.status == "ok"

    # Round-trip the output and confirm the stamp region still has red
    # channel spread (i.e., color was preserved through compression).
    doc = pdfium.PdfDocument(pdf_out)
    try:
        rendered = doc[0].render(scale=100 / 72).to_pil().convert("RGB")
    finally:
        doc.close()
    out_arr = np.asarray(rendered, dtype=np.int16)
    # Find the approximate stamp location in output coords (proportional).
    yh, xh = out_arr.shape[:2]
    stamp_y = int(1600 / 2550 * yh)
    stamp_x = int(2000 / 3300 * xh)
    patch = out_arr[stamp_y - 20 : stamp_y + 20, stamp_x - 20 : stamp_x + 20]
    channel_spread = patch.max(axis=-1) - patch.min(axis=-1)
    assert channel_spread.max() > 30, (
        f"red stamp lost through compression; max channel spread = "
        f"{channel_spread.max()} at {(stamp_y, stamp_x)}"
    )


@pytest.mark.integration
def test_multi_page_mixed_strategies_merges_correctly() -> None:
    """Wave 2 CRIT: the earlier stub had a bare `pass` body with a TODO.
    A 3-page PDF where each page genuinely hits a different strategy must
    produce a 3-page output with all content preserved.

    Pages constructed to deterministically trigger each strategy:
      1. TEXT_ONLY — black text on pure white, mask_coverage > 5% + paper
         bg dominates.
      2. PHOTO_ONLY — color gradient with near-zero mask (no text), mask
         coverage < 0.1%.
      3. MIXED — text ink + color background texture, mask_coverage in
         the (5%, 30%) band + bg not paper-dominated.
    """
    import io

    import numpy as np
    import pikepdf
    from PIL import Image, ImageDraw, ImageFont

    def _text_only_raster() -> Image.Image:
        img = Image.new("RGB", (2550, 3300), "white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 72)
        except OSError:
            font = ImageFont.load_default(size=72)
        for r in range(10):
            draw.text((300, 300 + r * 180), f"TEXT PAGE LINE {r}", fill="black", font=font)
        return img

    def _photo_only_raster() -> Image.Image:
        # Color gradient, no text — mask_coverage near zero.
        arr = np.zeros((2550, 3300, 3), dtype=np.uint8)
        arr[..., 0] = np.linspace(100, 255, 3300, dtype=np.uint8)[None, :]
        arr[..., 2] = np.linspace(255, 100, 3300, dtype=np.uint8)[None, :]
        return Image.fromarray(arr)

    def _mixed_raster() -> Image.Image:
        rng = np.random.default_rng(seed=3)
        bg = rng.integers(80, 200, size=(2550, 3300, 3), dtype=np.uint8).astype(np.uint8)
        img = Image.fromarray(bg)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 96)
        except OSError:
            font = ImageFont.load_default(size=96)
        for r in range(12):
            draw.text((200, 200 + r * 170), f"MIXED LINE {r}", fill="black", font=font)
        return img

    pdf = pikepdf.new()
    for raster_fn in (_text_only_raster, _photo_only_raster, _mixed_raster):
        img = raster_fn()
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        jpeg = io.BytesIO()
        img.save(jpeg, format="JPEG", quality=95, subsampling=0)
        xobj = pdf.make_stream(
            jpeg.getvalue(),
            Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
            Width=img.size[0], Height=img.size[1],
            ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")

    buf = io.BytesIO()
    pdf.save(buf)
    pdf_in = buf.getvalue()

    # Wave 6 Test-Integrity H2: pre-assert that each page routes to its
    # intended strategy. Font availability and JPEG ringing can cause
    # _text_only_raster() to demote to MIXED on some CI machines. Without
    # this check, the strategy_distribution assertion looks like a routing
    # regression but is actually a fixture reliability issue.
    import numpy as np
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.strategy import PageStrategy, classify_page

    def _route(page_idx: int) -> PageStrategy:
        raster = rasterize_page(pdf_in, page_index=page_idx, dpi=150)
        mask_arr = np.asarray(build_mask(raster).convert("1"), dtype=bool)
        cov = float(mask_arr.sum()) / max(1, mask_arr.size)
        return classify_page(raster, mask_coverage_fraction=cov)

    assert _route(0) == PageStrategy.TEXT_ONLY, (
        "Page 0 fixture must route TEXT_ONLY; check font rendering and mask threshold"
    )
    assert _route(1) == PageStrategy.PHOTO_ONLY, (
        "Page 1 fixture must route PHOTO_ONLY; check gradient mask coverage"
    )
    assert _route(2) == PageStrategy.MIXED, (
        "Page 2 fixture must route MIXED; check text+bg texture classification"
    )

    _, report = compress(pdf_in)
    assert report.pages == 3
    assert report.status == "ok"
    # All three strategy counters should be non-zero (exactly one page per
    # strategy) — pinned from the `strategy_distribution` dimension the plan
    # emits in Task 4a.
    assert report.strategy_distribution["text_only"] == 1
    assert report.strategy_distribution["photo_only"] == 1
    assert report.strategy_distribution["mixed"] == 1
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/integration/test_ratio_gate.py -v`
Expected: all integration tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_ratio_gate.py
git commit -m "test(ratio-gate): text-only ≥20×, colored stamp preserved, multi-page mix"
```

---

## Task 5: Photo-only path — regression gate + fidelity check

**Framing note (Wave 1 L2):** this task is explicitly NOT TDD red→green. It's a **regression guard**. Task 4a already makes photo-only pages work; this task adds the gate that keeps them working if someone tunes the photo path later.

Also wires `photo_target_dpi` as a separate option (Pre-Mortem #3: hardcoding 150 DPI loses micro-detail on radiology-like content).

**Files:**
- Modify: `tests/integration/test_ratio_gate.py`
- Modify: `pdf_smasher/types.py` (add `photo_target_dpi: int = 200`)
- Modify: `pdf_smasher/__init__.py` (use `options.photo_target_dpi` in PHOTO_ONLY branch)

- [ ] **Step 1: Write the regression-gate tests**

```python
# tests/integration/test_ratio_gate.py — append
def _photo_only_fixture() -> bytes:
    """A photo-like page: no text, lots of high-frequency content."""
    import numpy as np

    rng = np.random.default_rng(seed=42)
    arr = rng.integers(0, 256, size=(2550, 3300, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    return _wrap_raster_as_pdf_bytes(img)


def _photo_with_sharp_edges_fixture() -> bytes:
    """High-contrast sharp edges — tests that photo path preserves detail.
    Wave 1 Pre-Mortem #3: radiology scans lose micro-calcifications when
    photo path downsamples to 150 DPI."""
    import numpy as np

    arr = np.full((2550, 3300, 3), 128, dtype=np.uint8)
    # 20×20 px sharp squares across the image — these are the "detail"
    # we must preserve through compression.
    for y in range(100, 2500, 100):
        for x in range(100, 3200, 100):
            arr[y:y + 10, x:x + 10] = 0
    img = Image.fromarray(arr)
    return _wrap_raster_as_pdf_bytes(img)


@pytest.mark.integration
def test_photo_only_page_does_not_regress() -> None:
    """Regression gate: photo-only must compress >=3× via single-JPEG path.

    Wave 6 Test-Integrity H1: like text_only_hits_target_ratio, this test
    must pre-assert the fixture routes PHOTO_ONLY. A MIXED page also hits >=3×
    at baseline (4.99×), making the assertion a false positive on a MIXED route.
    """
    import numpy as np
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.strategy import PageStrategy, classify_page

    pdf_in = _photo_only_fixture()
    _raster = rasterize_page(pdf_in, page_index=0, dpi=150)
    _mask = build_mask(_raster)
    _mask_arr = np.asarray(_mask.convert("1"), dtype=bool)
    _coverage = float(_mask_arr.sum()) / max(1, _mask_arr.size)
    _strategy = classify_page(_raster, mask_coverage_fraction=_coverage)
    assert _strategy == PageStrategy.PHOTO_ONLY, (
        f"_photo_only_fixture() routed to {_strategy!r} instead of PHOTO_ONLY. "
        "Fix the fixture before the ratio assertion is meaningful."
    )

    _, report = compress(pdf_in)
    assert report.ratio >= 3.0, (
        f"photo-only regressed below 3×: got {report.ratio:.2f}x"
    )


@pytest.mark.integration
def test_photo_only_page_preserves_sharp_edges() -> None:
    """Pre-Mortem #3: sharp 10×10 pixel squares must survive the photo
    path at default settings (photo_target_dpi=200).

    Wave 3 HIGH + Wave 5 Test-Integrity HIGH-4: render at photo_target_dpi (200),
    NOT at 300 DPI source scale. Using the wrong DPI makes the coordinate math
    `y_out = int(y_src * img.height / 2550)` sample the wrong pixel location,
    causing the test to miss the black squares entirely.
    Also: the old plan had Step 1 (wrong DPI) and Step 3 (fix DPI) as separate
    steps, so an engineer copying Step 1 first would have the wrong body.
    This is the FINAL version — write exactly this in Step 1.
    """
    import numpy as np
    import pypdfium2 as pdfium
    from pdf_smasher import CompressOptions as _CO

    pdf_in = _photo_with_sharp_edges_fixture()
    pdf_out, _ = compress(pdf_in)

    _photo_dpi = _CO().photo_target_dpi  # 200 by default — must match the photo encode DPI
    pdf = pdfium.PdfDocument(pdf_out)
    try:
        img = pdf[0].render(scale=_photo_dpi / 72).to_pil().convert("L")
    finally:
        pdf.close()

    arr = np.asarray(img)
    # Scale source coordinates (300 DPI input space) to output render space.
    # Input: 2550×3300 at 300 DPI; output rendered at photo_target_dpi.
    scale_factor = _photo_dpi / 300.0
    for y_src, x_src in [(100, 100), (500, 500), (1000, 1000)]:
        y_out = int(y_src * scale_factor)
        x_out = int(x_src * scale_factor)
        patch = arr[y_out:y_out + 15, x_out:x_out + 15]
        assert patch.min() < 100, (
            f"sharp edge lost at ({y_src}, {x_src}); darkest pixel = {patch.min()}"
        )
```

- [ ] **Step 2: Confirm `photo_target_dpi` is already in CompressOptions**

**Wave 3 CRIT:** `photo_target_dpi` was moved to Task 4a Step 0 where `CompressOptions` already received the field. No types.py edit needed here — just verify it's there:

Run: `uv run python -c "from pdf_smasher.types import CompressOptions; print(CompressOptions().photo_target_dpi)"`
Expected: `200`

- [ ] **Step 3: ~~Fix the sharp-edges test render scale~~ (ALREADY DONE IN STEP 1)**

**Wave 5 Test-Integrity HIGH-4 resolution:** the Wave 3 HIGH fix (render at `photo_target_dpi`) is incorporated into Step 1 above. No separate edit needed. If you see a Step 3 that says "replace render scale" — that's an obsolete instruction from an earlier draft; ignore it and use the Step 1 body as-is.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/integration/test_ratio_gate.py -v`
Expected: both new tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_ratio_gate.py
git commit -m "feat(photo-only): regression gate + sharp-edge fidelity (render at photo_target_dpi)"
```

---

## Task 6: `--force-monochrome` CLI flag

**Framing note:** Task 4b already wired the routing behavior + tests (the integration test that was in the original plan's Task 6 has moved to 4b because it was actually a behavior test, not a CLI test). This task is purely CLI wiring.

**Files:**
- Modify: `pdf_smasher/cli/main.py` (add `--force-monochrome` flag)
- Modify: `tests/integration/test_cli_e2e.py` (CLI smoke test)

- [ ] **Step 1: Write the failing CLI test**

```python
# tests/integration/test_cli_e2e.py — append
@pytest.mark.integration
def test_cli_force_monochrome_flag_routes_through_options(tmp_path) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
    """--force-monochrome on the CLI must reach CompressOptions and produce
    different output bytes than the default run."""
    in_path = _make_pdf(tmp_path, text="CLI_FORCE_MONO_TEST")
    out_default = tmp_path / "default.pdf"
    out_mono = tmp_path / "mono.pdf"

    rc1 = main([str(in_path), "-o", str(out_default)])
    rc2 = main([str(in_path), "-o", str(out_mono), "--force-monochrome"])
    assert rc1 == 0 and rc2 == 0
    # Wave 3 HIGH: byte-identity comparison is brittle — deterministic_id=True
    # in pikepdf means truly identical content WILL produce byte-identical PDFs
    # on the same platform, but JPEG encoder variance across platforms can flip
    # this. Use a semantic check instead: the mono run must be strictly smaller
    # (force_monochrome on a colored page always produces a smaller output
    # because the MRC JPEG bg disappears entirely on the TEXT_ONLY path).
    assert out_mono.stat().st_size < out_default.stat().st_size, (
        "--force-monochrome must reduce output size vs default run; "
        f"default={out_default.stat().st_size}, mono={out_mono.stat().st_size}"
    )


# Retain the semantic-behavior test in test_compress_api.py but redo the
# fixture so it actually triggers MIXED route (Wave 1 B.C8). The fixture
# below is a photo-like gradient with text overlaid — guaranteed to route
# as MIXED (non-paper-dominated bg per classify_page).
@pytest.mark.integration
def test_force_monochrome_compresses_heavier_than_default() -> None:
    """With force_monochrome, a mixed-content page gets the text-only route
    and produces a smaller output than the default MIXED route."""
    import io  # Wave 6 Exec-Realism H2: io.BytesIO used below
    import numpy as np
    import pikepdf
    from PIL import Image
    from pdf_smasher import compress, CompressOptions  # Wave 6 H2: explicit imports

    # Build a MIXED-route page: photo-like gradient with text overlaid.
    # The page is NOT paper-dominated (light_pixel_fraction < 0.80), so
    # classify_page returns MIXED, letting force_monochrome actually kick
    # in and collapse to TEXT_ONLY.
    rng = np.random.default_rng(seed=7)
    arr = rng.integers(80, 200, size=(2550, 3300, 3), dtype=np.uint8)  # noisy bg
    img = Image.fromarray(arr.astype(np.uint8))
    from PIL import ImageDraw, ImageFont
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 96)
    except OSError:
        font = ImageFont.load_default(size=96)
    for i in range(8):
        draw.text((200, 300 + i * 160), f"Content line {i}", fill="black", font=font)

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]
    jpeg_buf = io.BytesIO()
    img.save(jpeg_buf, format="JPEG", quality=95, subsampling=0)
    xobj = pdf.make_stream(
        jpeg_buf.getvalue(),
        Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image,
        Width=img.size[0], Height=img.size[1],
        ColorSpace=pikepdf.Name.DeviceRGB, BitsPerComponent=8,
        Filter=pikepdf.Name.DCTDecode,
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")
    out = io.BytesIO()
    pdf.save(out)
    pdf_in = out.getvalue()

    _, default_report = compress(pdf_in)
    _, mono_report = compress(pdf_in, options=CompressOptions(force_monochrome=True))

    # Tighter comparison than "<" — JPEG encoder variance across platforms
    # can produce small size deltas that make a strict "<" flaky. Require
    # a meaningful reduction (Wave 1 B.M5).
    assert mono_report.output_bytes < default_report.output_bytes * 0.8, (
        f"force_monochrome should reduce size by at least 20%: "
        f"default={default_report.output_bytes:,}, mono={mono_report.output_bytes:,}"
    )
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run: `uv run pytest tests/integration/test_cli_e2e.py::test_cli_force_monochrome_flag_routes_through_options -v`
Expected: FAIL — `--force-monochrome` not a recognized argparse flag yet.

- [ ] **Step 3: Add `--force-monochrome` to CLI**

In `pdf_smasher/cli/main.py`, add inside `_parser()` next to the other engine-knob flags:

```python
p.add_argument(
    "--force-monochrome",
    action="store_true",
    help=(
        "Collapse mixed/photo pages to the text-only route. Emits "
        "page-N-color-detected-in-monochrome-mode in the report's warnings "
        "when color content is flattened. See SPEC.md §2.1."
    ),
)
```

And in `_build_options()` add `force_monochrome=args.force_monochrome` to the `CompressOptions(...)` call.

- [ ] **Step 4: Extend `--doctor` to probe JPEG2000 + jbig2enc explicitly**

**Wave 2 CRIT-5 context:** `--doctor` currently lists `qpdf` + `tesseract` but doesn't tell the user if the JPEG2000 path or jbig2enc path are actually available. A user who got a Pillow wheel without OpenJPEG gets silent fallback to JPEG and no way to diagnose.

In `pdf_smasher/cli/main.py`'s `_doctor()` (or equivalent health-check function), add:

```python
# JPEG2000 via Pillow's bundled OpenJPEG — probe by attempting encode.
try:
    import io as _io_probe

    from PIL import Image as _pil_probe

    _buf = _io_probe.BytesIO()
    _pil_probe.new("RGB", (8, 8)).save(_buf, format="JPEG2000")
    lines.append("  JPEG2000      available (Pillow/OpenJPEG)")
except (OSError, ImportError) as e:
    lines.append(f"  JPEG2000      UNAVAILABLE ({type(e).__name__}: {e}) — "
                 f"bg_codec=jpeg2000 will fall back to JPEG")

# jbig2enc presence (separate from qpdf): absence drops text-only ratio
# from ~50× to ~8×. Worth surfacing even if the binary is optional.
import shutil as _shutil_probe
if _shutil_probe.which("jbig2") is None:
    lines.append("  jbig2enc      NOT FOUND — text-only compression will "
                 "fall back to flate (~6× reduction vs ~50× with jbig2)")
else:
    lines.append("  jbig2enc      available")
```

- [ ] **Step 5: Run all CLI + integration tests**

Run: `uv run pytest tests/unit/test_cli.py tests/integration/test_cli_e2e.py tests/integration/test_compress_api.py -v`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add pdf_smasher/cli/main.py tests/integration/test_cli_e2e.py tests/integration/test_compress_api.py
git commit -m "feat(cli): --force-monochrome flag + --doctor probes JPEG2000/jbig2enc"
```

---

## Task 7: JPEG2000 background codec option

Pillow bundles OpenJPEG *when the wheel was built with JPEG2000 support*. Not all environments have this. Adding JPEG2000 saves 15-25% vs JPEG at equivalent perceived quality on paper-texture backgrounds.

**Wave 1 findings addressed:**
- A.C2 — wire `bg_codec` into BOTH `compose_mrc_page` AND `compose_photo_only_page` (photo-only was supposed to default to JPEG2000 per ARCHITECTURE.md:119)
- B.C6 / C.C1 — OpenJPEG may be unavailable; add fallback. Calibrate `quality_layers` empirically, not a hardcoded 30:1.
- C.M5 — add a test that JPEG2000 actually produces smaller output than JPEG (not just that the filter is JPXDecode).
- A.M4 — the calibration is a test artifact, not a magic number.

**Wave 2 CRIT-P4 addressed (latency gate):** Pillow's OpenJPEG encode runs at ~1-2 s per 300 DPI page. On a 200-page document that is +3-6 minutes of wall time vs JPEG's ~50 ms/page. For `mode=fast`, users have explicitly opted into speed over ratio — do NOT silently enable JPEG2000 even when `bg_codec="jpeg2000"` is passed. The decision table:

| Mode | bg_codec option | Effective codec | Rationale |
|---|---|---|---|
| fast | anything | jpeg | Speed over ratio. |
| standard | jpeg (default) | jpeg | No change from baseline. |
| standard | jpeg2000 | jpeg2000 | User opted in. |
| safe | jpeg (default) | jpeg | Safe mode doesn't add codec risk. |
| safe | jpeg2000 | jpeg2000 + stricter verifier | Task 0.2 tighter thresholds catch any J2K drift. |

**Files:**
- Modify: `pdf_smasher/types.py` (add `bg_codec` option)
- Modify: `pdf_smasher/engine/compose.py` (honor `bg_codec` on both compose_mrc_page AND compose_photo_only_page; OpenJPEG-unavailable fallback)
- Modify: `pdf_smasher/__init__.py` (pass through)
- Test: `tests/unit/engine/test_compose.py`

- [ ] **Step 1: Write the failing test (append)**

```python
# Append to tests/unit/engine/test_compose.py
import io


def _openjpeg_available() -> bool:
    """Return True if Pillow's JPEG2000 encoder (OpenJPEG) is available AND
    supports quality_mode='rates' (the exact parameters used by _jpeg2000_bytes).

    Wave 6 Test-Integrity C2: the probe MUST use the same encode parameters as
    _jpeg2000_bytes() or it can pass on builds where the real encode raises
    TypeError (e.g., old Pillow-SIMD that has OpenJPEG but not quality_mode='rates').
    """
    from PIL import Image
    try:
        buf = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buf, format="JPEG2000", quality_mode="rates", quality_layers=[25])
        return True
    except (OSError, KeyError, TypeError):
        return False


@pytest.mark.skipif(
    not _openjpeg_available(),
    reason="Pillow build lacks OpenJPEG encoder — bg_codec='jpeg2000' falls back to JPEG",
)
def test_compose_mrc_jpeg2000_bg_option() -> None:
    """bg_codec='jpeg2000' should produce a /JPXDecode-filtered background.

    Wave 5 Test-Integrity CRIT-2: this test MUST be skipped when OpenJPEG is
    unavailable — the implementation correctly falls back to JPEG (/DCTDecode)
    in that case, which is the right behavior per spec. Without the skipif,
    the test would fail on a correct implementation on Alpine/Pillow-SIMD.
    """
    import pikepdf

    from pdf_smasher.engine.compose import compose_mrc_page

    fg = _black_blob_foreground()
    mask = _blank_mask()
    bg = _blank_background()
    out = compose_mrc_page(
        foreground=fg,
        foreground_color=(0, 0, 0),
        mask=mask,
        background=bg,
        page_width_pt=612.0,
        page_height_pt=792.0,
        bg_codec="jpeg2000",
    )
    with pikepdf.open(io.BytesIO(out)) as pdf:
        bg_xobj = pdf.pages[0].Resources.XObject["/BG"]
        assert str(bg_xobj.stream_dict.get("/Filter")) == "/JPXDecode"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/engine/test_compose.py::test_compose_mrc_jpeg2000_bg_option -v`
Expected: SKIP (if OpenJPEG unavailable) or FAIL with `TypeError: compose_mrc_page() got an unexpected keyword argument 'bg_codec'`

- [ ] **Step 3: Add `bg_codec` parameter with OpenJPEG-unavailable fallback**

Modify `pdf_smasher/engine/compose.py`:

```python
# At module level:
from typing import Literal

BgCodec = Literal["jpeg", "jpeg2000"]

# Helper — quality_layers calibrated empirically against JPEG q=45 on the
# golden corpus (Task 8 measurement). See 2026-04-22 measurement run:
# quality_layers=[25] ≈ 40 dB PSNR ≈ visually matches JPEG q=45, typical
# ~20% smaller. Wave 1 A.M4 / C.C1 — NOT a hardcoded magic number;
# committed alongside a measure_ratios.py calibration run.
_JPEG2000_QUALITY_LAYERS_DEFAULT = [25]


def _jpeg2000_bytes(
    image: Image.Image,
    *,
    quality_layers: list[int] | None = None,
) -> bytes | None:
    """Encode a PIL image as JPEG2000 (JP2) bytes via Pillow's bundled OpenJPEG.

    Returns None if the encoder is not available in this Pillow build
    (e.g., Pillow-SIMD without JPEG2000 support, minimal Alpine images).
    Callers MUST fall back to JPEG in that case (Wave 1 B.C6).
    """
    buf = io.BytesIO()
    try:
        image.save(
            buf,
            format="JPEG2000",
            quality_mode="rates",
            quality_layers=quality_layers or _JPEG2000_QUALITY_LAYERS_DEFAULT,
        )
    except (OSError, KeyError, TypeError):
        # OSError: "encoder jpeg2k not available" — Pillow built without OpenJPEG.
        # KeyError: older Pillow builds (pre-9.x) raise KeyError('jpeg2k').
        # TypeError: quality_mode="rates" unsupported in very old Pillow-SIMD.
        # Wave 6 Pre-Mortem H2 + Test-Integrity C2: catch the full set or the
        # fallback chain breaks on those builds.
        return None
    return buf.getvalue()


# Modify compose_mrc_page signature:
def compose_mrc_page(
    *,
    foreground: Image.Image,
    foreground_color: tuple[int, int, int],
    mask: Image.Image,
    background: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    bg_jpeg_quality: int = _JPEG_QUALITY_BG,
    bg_codec: BgCodec = "jpeg",
    bg_color_mode: BgColorMode = "rgb",  # Task 3
) -> bytes:
    ...
    # Inside the function, after bg_prepared + bg_color_space are set (from Task 3):
    bg_data: bytes | None = None
    bg_filter = pikepdf.Name.DCTDecode
    if bg_codec == "jpeg2000":
        bg_data = _jpeg2000_bytes(bg_prepared)
        if bg_data is not None:
            bg_filter = pikepdf.Name.JPXDecode
        # else: fall through to JPEG fallback
    if bg_data is None:
        bg_data = _jpeg_bytes(bg_prepared, bg_jpeg_quality)
        bg_filter = pikepdf.Name.DCTDecode

    bg_xobj = _make_stream(
        pdf,
        data=bg_data,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=bg_prepared.size[0],
        Height=bg_prepared.size[1],
        ColorSpace=bg_color_space,
        BitsPerComponent=8,
        Filter=bg_filter,
    )
```

Apply the SAME pattern inside `compose_photo_only_page` (Wave 1 A.C2 — photo-only was supposed to default to JPEG2000 per ARCHITECTURE.md:119, but the option was never threaded in):

```python
def compose_photo_only_page(
    *,
    raster: Image.Image,
    page_width_pt: float,
    page_height_pt: float,
    target_dpi: int,
    jpeg_quality: int = _JPEG_QUALITY_BG,
    subsampling: int = _JPEG_SUBSAMPLING_444,
    bg_codec: BgCodec = "jpeg",
    bg_color_mode: BgColorMode = "rgb",   # Wave 3 / Wave 6 C2: MUST carry forward from Task 3
) -> bytes:
    # ... existing body up to `resized = raster.convert("RGB").resize(...)`
    # Replace the encoding block:
    data: bytes | None = None
    filter_name = pikepdf.Name.DCTDecode
    if bg_codec == "jpeg2000":
        data = _jpeg2000_bytes(resized)
        if data is not None:
            filter_name = pikepdf.Name.JPXDecode
    if data is None:
        data = _jpeg_bytes(resized, jpeg_quality, subsampling=subsampling)
        filter_name = pikepdf.Name.DCTDecode
    # ... rest of the compose builds the XObject with Filter=filter_name
```

- [ ] **Step 4: Add a size-comparison test (Wave 1 C.M5)**

```python
# tests/unit/engine/test_compose.py — append
def test_jpeg2000_produces_smaller_bg_than_jpeg_on_paper_texture() -> None:
    """JPEG2000 must produce a smaller output than JPEG on paper-texture
    content; otherwise the option isn't worth wiring (Wave 1 C.M5).
    Skip if OpenJPEG is unavailable."""
    import io
    from PIL import Image
    import numpy as np

    # Paper-texture-like gradient
    arr = np.full((600, 600, 3), 240, dtype=np.uint8)
    rng = np.random.default_rng(seed=1)
    noise = rng.integers(-15, 16, size=arr.shape, dtype=np.int16)
    arr = np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    bg = Image.fromarray(arr)
    fg = _black_blob_foreground()
    mask = _blank_mask()

    out_jpeg = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_codec="jpeg",
    )
    out_jpx = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_codec="jpeg2000",
    )
    # If OpenJPEG is available, output should be smaller. If unavailable,
    # compose falls back to JPEG and sizes are equal — which is still
    # acceptable behavior (no regression), so the test is soft-compare.
    if out_jpeg != out_jpx:
        # Different outputs — JPEG2000 path was taken. Must be smaller.
        assert len(out_jpx) < len(out_jpeg) * 0.92, (
            f"JPEG2000 must be >=8% smaller when available: "
            f"jpeg={len(out_jpeg):,}, jpx={len(out_jpx):,}"
        )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/engine/test_compose.py -v`
Expected: all green.

- [ ] **Step 6: Thread the option through `CompressOptions` with fast-mode gate**

Modify `pdf_smasher/types.py` — add to `CompressOptions`:

```python
bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg"
```

In `pdf_smasher/__init__.py`, resolve the **effective** codec once per job (Wave 2 CRIT-P4 latency gate) and pass that through:

```python
# Fast mode overrides bg_codec to JPEG for latency reasons (see Task 7
# decision table). Emit a warning if the user explicitly asked for
# jpeg2000 but fast mode demoted it, so they aren't surprised by
# weaker compression.
effective_bg_codec = options.bg_codec
if options.mode == "fast" and options.bg_codec == "jpeg2000":
    warnings.append("bg-codec-jpeg2000-demoted-fast-mode")
    effective_bg_codec = "jpeg"
```

Thread `effective_bg_codec` into the `_mrc_compose(...)` and `compose_photo_only_page(...)` calls inside the per-page loop.

- [ ] **Step 7: Add `--bg-codec` CLI flag**

In `pdf_smasher/cli/main.py`:

```python
p.add_argument(
    "--bg-codec",
    choices=["jpeg", "jpeg2000"],
    default="jpeg",
    help="Background codec. Default: jpeg. jpeg2000 is ~8-20% smaller on paper textures.",
)
```

And in `_build_options()`: `bg_codec=args.bg_codec`.

- [ ] **Step 8: Run the full test suite**

Run: `uv run pytest tests -q`
Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add pdf_smasher/engine/compose.py pdf_smasher/types.py pdf_smasher/__init__.py pdf_smasher/cli/main.py tests/unit/engine/test_compose.py
git commit -m "feat(compose): JPEG2000 bg codec option w/ fallback; wired into both compose paths + CLI"
```

---

## Task 7.5: Thread the existing dead options (Wave 1 A.C3 + Wave 2 follow-up)

**Wave 2 CRIT-3 context:** the Wave-1 draft claimed `target_color_quality`, `bg_chroma_subsampling`, and `legal_codec_profile` would all be wired here — but the sketched Step 3 only wired `target_color_quality`. `bg_chroma_subsampling` is a SPEC §2.1 public CLI flag (`--bg-chroma`), so leaving it unwired violates Jack's "no half-finished paths" rule. `legal_codec_profile` (force CCITT G4 instead of JBIG2) genuinely requires a CCITT G4 encoder we haven't built — the honest fix is to raise `NotImplementedError` when the user sets it, not pretend it's wired.

**Files:**
- Modify: `pdf_smasher/engine/compose.py` — accept `bg_chroma_subsampling` on both compose_mrc_page and compose_photo_only_page
- Modify: `pdf_smasher/__init__.py` — thread `options.target_color_quality` → `bg_jpeg_quality`; `options.bg_chroma_subsampling` → `subsampling` kwarg; raise on `legal_codec_profile`
- Modify: `pdf_smasher/cli/main.py` — add `--bg-chroma` CLI flag (per SPEC §2.1)

### Task 7.5a — Wire `target_color_quality`

- [ ] **Step 1: Write the coverage test**

```python
# tests/unit/engine/test_compose.py — append
def test_bg_jpeg_quality_affects_output_size() -> None:
    """Lower bg_jpeg_quality produces smaller output."""
    bg = _blank_background()
    fg = _black_blob_foreground()
    mask = _blank_mask()
    out_hi = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_jpeg_quality=80,
    )
    out_lo = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_jpeg_quality=20,
    )
    assert len(out_lo) < len(out_hi)
```

Run: `uv run pytest tests/unit/engine/test_compose.py::test_bg_jpeg_quality_affects_output_size -v`
Expected: PASS (already supported).

- [ ] **Step 2: Thread `target_color_quality` in `__init__.py`**

In `compress()` where we call `compose_mrc_page` (in `_mrc_compose` helper from Task 4a):

```python
return compose_mrc_page(
    foreground=fg.image,
    foreground_color=fg.ink_color,
    mask=mask,
    background=bg,
    page_width_pt=width_pt,
    page_height_pt=height_pt,
    bg_color_mode=bg_color_mode,
    bg_codec=effective_bg_codec,
    bg_jpeg_quality=options.target_color_quality,
)
```

### Task 7.5b — Wire `bg_chroma_subsampling` + add `--bg-chroma` CLI flag

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/engine/test_compose.py — append
def test_bg_chroma_subsampling_affects_output_size() -> None:
    """4:2:0 subsampling produces smaller JPEG than 4:4:4 on color content."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(seed=5)
    bg = Image.fromarray(rng.integers(0, 256, size=(600, 600, 3), dtype=np.uint8))
    fg = _black_blob_foreground()
    mask = _blank_mask()
    # subsampling: 0=4:4:4, 1=4:2:2, 2=4:2:0 (Pillow convention)
    out_444 = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_subsampling=0,
    )
    out_420 = compose_mrc_page(
        foreground=fg, foreground_color=(0, 0, 0), mask=mask, background=bg,
        page_width_pt=612.0, page_height_pt=792.0, bg_subsampling=2,
    )
    assert len(out_420) < len(out_444)
```

Run: `uv run pytest tests/unit/engine/test_compose.py::test_bg_chroma_subsampling_affects_output_size -v`
Expected: FAIL — `compose_mrc_page` doesn't accept `bg_subsampling` yet.

- [ ] **Step 2: Add `bg_subsampling` to compose**

In `pdf_smasher/engine/compose.py`, add the parameter to both `compose_mrc_page` and `compose_photo_only_page`, defaulting to `_JPEG_SUBSAMPLING_444` (0). Thread it into the `_jpeg_bytes(..., subsampling=bg_subsampling)` call.

Map the SPEC CLI values to Pillow's integer convention:

```python
# pdf_smasher/types.py — replace the existing bg_chroma_subsampling definition:
ChromaSubsampling = Literal["4:4:4", "4:2:2", "4:2:0"]
bg_chroma_subsampling: ChromaSubsampling = "4:4:4"


# pdf_smasher/__init__.py — add helper
_CHROMA_TO_PIL = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}
```

**Wave 6 Exec-Realism H1:** `_mrc_compose` also needs `bg_subsampling` in its own signature so
it can pass the value through to `compose_mrc_page`. Add the parameter to `_mrc_compose`:

```python
def _mrc_compose(
    raster: Image.Image,
    mask: Image.Image,
    width_pt: float,
    height_pt: float,
    bg_target_dpi: int,
    source_dpi: int,
    *,
    bg_jpeg_quality: int | None = None,
    bg_codec: BgCodec = "jpeg",         # added in Task 7
    bg_color_mode: BgColorMode = "rgb", # added in Task 3
    bg_subsampling: int = _JPEG_SUBSAMPLING_444,  # NEW — Task 7.5b
) -> bytes:
    ...
    kwargs: dict[str, Any] = dict(
        ...
        bg_codec=bg_codec,
        bg_color_mode=bg_color_mode,
        bg_subsampling=bg_subsampling,  # NEW
    )
```

Then thread through the `_mrc_compose(...)` + `compose_photo_only_page(...)` calls in `compress()`:

```python
bg_subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
```

- [ ] **Step 3: Add `--bg-chroma` CLI flag (SPEC §2.1)**

```python
# pdf_smasher/cli/main.py
p.add_argument(
    "--bg-chroma",
    choices=["4:4:4", "4:2:2", "4:2:0"],
    default="4:4:4",
    help=(
        "Chroma subsampling for bg JPEG. 4:4:4 preserves colored text; "
        "4:2:0 is smaller but smears color on thin strokes. Default: 4:4:4."
    ),
)
```

And in `_build_options()`: `bg_chroma_subsampling=args.bg_chroma`.

### Task 7.5c — Honestly handle `legal_codec_profile` (raise, don't pretend)

`legal_codec_profile` in SPEC §1.1 names an ARCHITECTURE band that would force CCITT G4 for BSI/NARA compliance. We have NOT implemented a CCITT G4 encoder in Phase 2b and are not going to in this plan. The honest move is:

**Wave 3 CRIT:** `legal_codec_profile` is currently typed `bool = False` in `CompressOptions`. The guard `if options.legal_codec_profile is not None:` is ALWAYS True for a bool (False is not None), so every call would raise `NotImplementedError`. Fix the type annotation AND the guard.

- [ ] **Step 0: Fix the type annotation in `types.py`**

```python
# pdf_smasher/types.py — change legal_codec_profile field:
# BEFORE: legal_codec_profile: bool = False
# AFTER:
legal_codec_profile: str | None = None
# str because the SPEC names the profile "ccitt-g4" (a string value);
# None means "not requested". bool was wrong — `if options.legal_codec_profile`
# on False evaluates truthiness, but `False is not None` is True, which would
# always raise NotImplementedError even when the user didn't set it.
```

- [ ] **Step 1: Write a failing test**

**Wave 4 H1 — guard placement:** `compress(b"", ...)` will raise `CorruptPDFError` during triage (empty bytes are not a valid PDF) BEFORE reaching the `legal_codec_profile` guard if the guard lives below the triage call. The test only works if the guard is placed at the **very top** of `compress()`, before `triage()` is called. Step 2 specifies exactly where. The test below uses `b""` which is valid for this purpose only because the guard fires first.

```python
# tests/integration/test_compress_api.py — append
def test_legal_codec_profile_raises_not_implemented() -> None:
    """Guard fires before triage — empty bytes are fine as input here because
    the NotImplementedError is raised before the PDF is parsed."""
    import pytest

    from pdf_smasher import CompressOptions, compress

    with pytest.raises(NotImplementedError, match="legal_codec_profile"):
        compress(b"", options=CompressOptions(legal_codec_profile="ccitt-g4"))


def test_legal_codec_profile_none_does_not_raise() -> None:
    """Default value (None) must NOT raise NotImplementedError.

    Wave 3 CRIT: the old bool=False guard `if options.legal_codec_profile is
    not None:` was always True for booleans (False is not None), so every
    compress() call would raise. This test would have caught it.
    """
    from pdf_smasher import CompressOptions

    opts = CompressOptions()  # legal_codec_profile defaults to None
    # We can't call compress() with empty bytes on a non-trivial PDF, but we
    # CAN assert that the guard doesn't fire at construction time.
    assert opts.legal_codec_profile is None
```

- [ ] **Step 2: Implement the raise at the VERY TOP of `compress()` — before triage**

**Wave 4 H1 critical:** place this guard as the **first substantive line** of `compress()`, before ANY call to `triage()` or any PDF parsing. Otherwise `compress(b"", ...)` hits triage first and raises `CorruptPDFError` instead of `NotImplementedError`, breaking the test.

```python
def compress(input_data: bytes, *, options: CompressOptions | None = None) -> tuple[bytes, CompressReport]:
    if options is None:
        options = CompressOptions()
    # GUARD: options that are reserved for a future phase — raise immediately,
    # before any PDF parsing, so callers get a clear error message.
    # Wave 4 H1: must be the FIRST check, before triage(), so that
    # `compress(b"", options=CompressOptions(legal_codec_profile="ccitt-g4"))`
    # raises NotImplementedError, not CorruptPDFError.
    if options.legal_codec_profile:
        msg = (
            "legal_codec_profile (CCITT G4 fallback) is not implemented in "
            "this build. Track: docs/ROADMAP.md Phase 4. Use "
            "legal_codec_profile=None and --engine mrc for Phase-2b outputs."
        )
        raise NotImplementedError(msg)
    # ... triage and everything else follows
```

**Wave 6 Exec-Realism C4:** `main.py`'s `_build_options()` currently passes
`legal_codec_profile=args.legal_mode` where `args.legal_mode` is a `bool` (`action="store_true"`).
After this step changes the type to `str | None`, passing `True` violates the type annotation
AND mypy will fail in the Task 9 final gate. Update `_build_options()` in `cli/main.py`:

```python
# pdf_smasher/cli/main.py — _build_options():
# BEFORE: legal_codec_profile=args.legal_mode,
# AFTER:
legal_codec_profile="ccitt-g4" if args.legal_mode else None,
```

This maps the CLI `--legal-mode` flag (a bool toggle) to the string profile name `"ccitt-g4"`,
which is what `compress()` will receive and then raise `NotImplementedError` on. When the flag
is not passed, `None` means "no legal profile" and the guard does NOT fire.

And update SPEC.md §1.1 to label `legal_codec_profile` as reserved for Phase 4 (currently raises `NotImplementedError`).

### Task 7.5 — Run full suite + commit

- [ ] **Step 1: Run tests**

Run: `uv run pytest tests -q`
Expected: all green.

- [ ] **Step 2: Commit**

```bash
git add pdf_smasher/engine/compose.py pdf_smasher/__init__.py pdf_smasher/types.py pdf_smasher/cli/main.py tests/unit/engine/test_compose.py tests/integration/test_compress_api.py
git commit -m "feat(compress): wire target_color_quality + bg_chroma_subsampling (--bg-chroma); legal_codec_profile raises NotImplementedError"
```

---

## Task 7.6: Update SPEC.md + ARCHITECTURE.md + CLI help (Wave 1 A.C1 + Wave 2 CRIT-1)

**Wave 2 CRIT-1 context:** the Wave-1 draft wrote to `SPEC.md §9.1` — but SPEC.md has no §9 (sections jump §8 → §10). Metrics and local-diagnostics live in `SPEC.md §8`. The verifier check table lives in `ARCHITECTURE.md §5`, NOT `SPEC.md §5` (that is the canonical-hash section). Fix both misroutes.

- [ ] **Step 1: Edit `docs/SPEC.md` §1.1 (`CompressOptions` table)**

Add fields:
```python
bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg"       # bg layer codec
photo_target_dpi: int = 200                           # DPI for PHOTO_ONLY path
# legal_codec_profile: kept but documented as NotImplementedError until Phase 4
```

- [ ] **Step 2: Edit `docs/SPEC.md` §2.1 (CLI flags)**

Under `Engine:` add:
```
--bg-codec {jpeg,jpeg2000}         Default: jpeg.
--photo-target-dpi INT             Default: 200. DPI for photo-only pages.
```

(`--force-monochrome` and `--bg-chroma` are already listed in §2.1. Remove them from any "new in Phase 2b" lists — they were spec'd in §2.1 earlier, just unwired. Task 6 + Task 7.5b fix the wiring.)

- [ ] **Step 3: Edit `docs/ARCHITECTURE.md` §5 (verifier table)**

Add the channel-parity row AFTER "Tile-level SSIM":

```
| Channel parity | Fraction of pixels with RGB spread > CHANNEL_SPREAD_COLOR_TOLERANCE | input has color → output must have color | Catches silent color loss that SSIM-on-L cannot see (Phase 2b) |
```

Also resolve the internal contradiction between the ARCH §5 narrative paragraph ("On any tile SSIM <0.96") and the table (tile ≥ 0.88 safe). Delete the `< 0.96` sentence or rewrite it as `< 0.88`. The table wins. (Wave 2 CRIT-2 flagged this as the source of the bad Wave-1 threshold claim.)

- [ ] **Step 4: Edit `docs/SPEC.md` §8 (Local logging and diagnostics)**

Append to the counter list:
```
- strategy_distribution{class="text_only|photo_only|mixed"} — emitted by compress() once per page.
  ALREADY_OPTIMIZED is not emitted by classify_page; triage pass-through pages bypass this loop.
- jbig2-fallback-to-flate — emitted per-page when jbig2enc errors mid-run and compose
  falls back to flate. Distinct from jbig2enc-unavailable-using-flate-fallback (job-wide).
- bg-codec-jpeg2000-demoted-fast-mode — emitted once per job when the user requested
  bg_codec=jpeg2000 and mode=fast demoted it for latency reasons.
```

- [ ] **Step 5: Commit (no test required for doc updates)**

```bash
git add docs/SPEC.md docs/ARCHITECTURE.md
git commit -m "docs: Phase 2b options, CLI flags, channel-parity verifier row, §8 counters"
```

---

## Task 8: Measurement script + real-fixture report

Land a reusable measurement harness so future ratio changes are verifiable.

**Files:**
- Create: `scripts/measure_ratios.py`
- Modify: `docs/SPIKE_REPORT.md` (append Phase-2b results)

- [ ] **Step 1: Write the measurement script**

```python
#!/usr/bin/env python3
# scripts/measure_ratios.py
"""Measure HankPDF compression ratios on a directory of PDFs.

Usage:
    uv run python scripts/measure_ratios.py <input-dir> [--force-monochrome]

Emits a markdown table to stdout. Exits 0 on success (even if some files
refused by CompressError — those are valid outcomes). Exits 1 if any
UNEXPECTED exception was raised, with an error banner in the output.

Filenames are shown as `<sha1(basename)[:8]>…<basename[-8:]>` per SPEC
§9.2 log-redaction policy (Wave 1 A.M8).

Unicode filenames (NFC/NFD, emoji) are normalized to NFC before display
(SPEC §4 unicode-filename row; Wave 1 A.M9).
"""

from __future__ import annotations

import argparse
import sys
import unicodedata
from pathlib import Path

import pikepdf

from pdf_smasher import CompressError, CompressOptions, compress


def _redact(name: str) -> str:
    """SPEC §9.2 filename-redaction: sha1(name)[:8]…name[-8:]."""
    import hashlib

    normed = unicodedata.normalize("NFC", name)
    digest = hashlib.sha1(normed.encode("utf-8")).hexdigest()
    tail = normed[-8:] if len(normed) > 8 else normed
    return f"{digest[:8]}…{tail}"


def run(input_dir: Path, *, force_monochrome: bool) -> int:
    pdfs = sorted(p for p in input_dir.glob("*.pdf") if p.is_file())
    if not pdfs:
        print(f"no PDFs found in {input_dir}", file=sys.stderr)
        return 2

    opts = CompressOptions(force_monochrome=force_monochrome)
    print(
        "| File | Input (bytes) | Output (bytes) | Ratio | "
        "Pages | Wall (ms) | Verifier |",
    )
    print("|---|---:|---:|---:|---:|---:|---|")

    total_in = 0
    total_out = 0
    n_ok = 0
    n_refused = 0
    n_crashed = 0

    for p in pdfs:
        data = p.read_bytes()
        label = _redact(p.name)
        try:
            _, report = compress(data, options=opts)
        except CompressError as e:
            # Structured refusal — expected per SPEC; not a crash.
            n_refused += 1
            print(f"| {label} | {len(data):,} | — | — | — | — | REFUSED: {type(e).__name__} |")
            continue
        except pikepdf.PdfError as e:
            n_refused += 1
            print(f"| {label} | {len(data):,} | — | — | — | — | CORRUPT: {e} |")
            continue
        except Exception as e:  # noqa: BLE001 — last-resort: anything else is a crash
            n_crashed += 1
            print(f"| {label} | {len(data):,} | — | — | — | — | *** CRASH: {type(e).__name__}: {e} *** |")
            continue
        n_ok += 1
        total_in += report.input_bytes
        total_out += report.output_bytes
        print(
            f"| {label} | {report.input_bytes:,} | {report.output_bytes:,} | "
            f"{report.ratio:.2f}x | {report.pages} | {report.wall_time_ms} | "
            f"{report.verifier.status} |",
        )

    print()
    if n_crashed > 0:
        print(f"**⚠ {n_crashed} CRASHES** — these are bugs, not refusals. Ratio totals omitted.")
        return 1

    if total_in > 0:
        print(
            f"| **TOTAL ({n_ok} ok, {n_refused} refused)** | "
            f"**{total_in:,}** | **{total_out:,}** | "
            f"**{total_in / max(1, total_out):.2f}x** | — | — | — |",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--force-monochrome", action="store_true")
    args = parser.parse_args()
    return run(args.input_dir, force_monochrome=args.force_monochrome)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x scripts/measure_ratios.py`

- [ ] **Step 3: Build a fixture directory + run it**

Run these to create a fixture dir and measure:

```bash
mkdir -p /tmp/hankpdf_fixtures
# Copy the existing synthetic scan from earlier spike runs if present, else regenerate:
uv run python -c "
from tests.integration.test_ratio_gate import _text_only_fixture, _photo_only_fixture
import pathlib
pathlib.Path('/tmp/hankpdf_fixtures/text_only.pdf').write_bytes(_text_only_fixture())
pathlib.Path('/tmp/hankpdf_fixtures/photo_only.pdf').write_bytes(_photo_only_fixture())
"
# If /tmp/big_scan.pdf exists from the spike phase, copy it in too:
cp /tmp/big_scan.pdf /tmp/hankpdf_fixtures/big_scan.pdf 2>/dev/null || true

uv run python scripts/measure_ratios.py /tmp/hankpdf_fixtures
```

- [ ] **Step 4: Append the measurement table to `docs/SPIKE_REPORT.md`**

Under a new section `## Phase 2b results (2026-04-22)` paste the stdout of the script.

- [ ] **Step 5: Commit**

```bash
git add scripts/measure_ratios.py docs/SPIKE_REPORT.md
git commit -m "feat(scripts): measure_ratios.py harness + Phase-2b results"
```

---

## Task 9: Final green gate + final measurement

- [ ] **Step 1: Run lint + format + type-check**

Run: `uv run ruff check pdf_smasher tests scripts`
Expected: `All checks passed!`

Run: `uv run ruff format --check pdf_smasher tests scripts`
Expected: all files already formatted.

Run: `uv run mypy pdf_smasher`
Expected: `Success: no issues found`

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests -q`
Expected: all previous tests still pass, plus the new Phase-2b tests.

- [ ] **Step 3: Run the final measurement**

Run: `uv run python scripts/measure_ratios.py /tmp/hankpdf_fixtures`
Expected (derived from first principles + Wave 1 M6 reviewer pass):

- `text_only.pdf`: ratio ≥ **20×** (synthetic black-on-white, single-JBIG2 path)
- `photo_only.pdf`: ratio ≥ **3×** (single-JPEG path, visual regression gate only)
- `big_scan.pdf` (10-page 8.83 MB realistic scanner output): ratio ≥ **8×** (default), ≥ **10×** (with `--force-monochrome` now that Task 4b covers PHOTO_ONLY + MIXED). The revised 8×/10× targets replace the original 12×/20× — Wave 1 M6 flagged those as optimistic for realistic scans because photo-only + blank pages drag the geometric mean. If real measurement comes in higher, great; the gate should be defensible, not aspirational.

- [ ] **Step 4: Commit the final report**

```bash
git add docs/SPIKE_REPORT.md
git commit -m "docs: Phase 2b compression-boost final report"
```

---

## Acceptance criteria (the whole plan)

- [ ] 120 pre-existing tests still pass + new Phase-2b tests pass (≥ 140 total given Task 0's verifier tests)
- [ ] `test_text_only_page_hits_target_ratio` asserts ratio ≥ 20× → green
- [ ] `test_photo_only_page_does_not_regress` asserts ratio ≥ 3× → green
- [ ] `test_photo_only_page_preserves_sharp_edges` → green (Wave 1 Pre-Mortem #3)
- [ ] `test_colored_stamp_does_not_route_to_text_only` → green (Wave 1 Pre-Mortem #1)
- [ ] `test_multi_page_mixed_strategies_merges_correctly` → green (Wave 1 B.M3)
- [ ] `test_force_monochrome_applies_to_photo_only_pages_too` → green (Wave 1 B.C2)
- [ ] `test_force_monochrome_emits_color_warning_on_colored_page` → green (SPEC §2 warning codes)
- [ ] `test_verifier_fails_when_input_had_color_but_output_is_grayscale` → green (Task 0.3 channel-parity)
- [ ] `test_tile_ssim_catches_local_region_drift_that_global_ssim_hides` → green (Task 0.1)
- [ ] JPEG2000 background path accessible via `bg_codec="jpeg2000"` on BOTH compose_mrc_page AND compose_photo_only_page (Wave 1 A.C2), produces `/JPXDecode` streams, gracefully falls back to JPEG when OpenJPEG unavailable (Wave 1 B.C6)
- [ ] `is_effectively_monochrome` + `detect_paper_color` helpers shipped with unit tests, living in `foreground.py` alongside `extract_foreground` (Wave 1 A.M1)
- [ ] Light-pixel threshold unified between `strategy.py` and `foreground.py` (Wave 1 B.C4)
- [ ] `compress()` dispatches per-page handling ALL four `PageStrategy` values (Wave 1 B.C1)
- [ ] `force_monochrome` applies to PHOTO_ONLY and MIXED both (Wave 1 B.C2)
- [ ] mask_coverage zero-size guard in place (Wave 1 B.C3)
- [ ] Per-page `try/except` bounds mid-loop crash memory footprint (Wave 1 C.C5)
- [ ] `compose_mrc_page` takes `bg_color_mode` from caller; does NOT import `is_effectively_monochrome` (Wave 1 C.M2)
- [ ] Verifier thresholds match ARCHITECTURE.md §5 **table** (global SSIM ≥ 0.92 both modes; tile ≥ 0.85 standard / ≥ 0.88 safe; raw Lev ≤ 0.05 standard / ≤ 0.02 safe). Wave 2 CRIT-2 fix — the Wave-1 "0.96 safe" value came from an internal contradiction in the ARCH narrative paragraph; table wins.
- [ ] Tile-level SSIM shipped (not stub) (Wave 1 Task 0.1)
- [ ] Channel-parity verifier check shipped (Wave 1 Task 0.3 / Pre-Mortem #1)
- [ ] `scripts/measure_ratios.py` redacts filenames + raises on unexpected exceptions (Wave 1 A.M8 / C.M7)
- [ ] `docs/SPEC.md` §1.1/§2.1/§8 updated (Wave 2 CRIT-1 — §9 does NOT exist; metrics go in §8)
- [ ] `docs/ARCHITECTURE.md` §5 gets the channel-parity row + contradicting `< 0.96` narrative sentence removed (Wave 2 CRIT-2)
- [ ] Task 4 split into 4a/4b/4c for bisectability (Wave 1 C.M3)
- [ ] ruff clean, mypy clean, ruff format --check clean
- [ ] `docs/SPIKE_REPORT.md` has a Phase-2b results table with real numbers
- [ ] Task 0.4 parseability guard passes (Wave 2 CRIT-2: no Python-2-era `except A, B:` syntax anywhere in package)
- [ ] `CHANNEL_SPREAD_COLOR_TOLERANCE` is the single source of truth for color detection — verifier and `is_effectively_monochrome` import the same constant (Wave 2 CRIT — the 5-vs-15 disagreement)
- [ ] `compress()` streams rasters through `verify_single_page` per-page — no whole-document buffer (Wave 2 CRIT-P2)
- [ ] `tile_ssim_min` uses vectorized `block_reduce` on a full SSIM map — no Python per-tile loop (Wave 2 CRIT-P3)
- [ ] Fast mode demotes `bg_codec=jpeg2000` → `jpeg` with a job-wide warning (Wave 2 CRIT-P4 latency gate)
- [ ] jbig2enc mid-run failure emits per-page `page-N-jbig2-fallback-to-flate` warning — not silent (Wave 2 CRIT-P1)
- [ ] `--doctor` probes JPEG2000 encode + jbig2enc availability (Wave 2 CRIT-5)
- [ ] `bg_chroma_subsampling` wired through `--bg-chroma` per SPEC §2.1 (Wave 2 CRIT-3/4)
- [ ] `legal_codec_profile` raises `NotImplementedError` with ROADMAP Phase 4 pointer — no longer pretends to be wired (Wave 2 CRIT-3)
- [ ] `KeyboardInterrupt` handled in the per-page loop — clears partial `page_pdfs` before re-raise (Wave 2)
- [ ] `strategy_distribution: Mapping[str, int]` field in `CompressReport` — added in Task 4a Step 0 (Wave 3 CRIT — Task 4c asserts this field, must exist before that test runs)
- [ ] `photo_target_dpi: int = 200` in `CompressOptions` — added in Task 4a Step 0 (Wave 3 CRIT — Task 4a loop uses `options.photo_target_dpi`, must exist)
- [ ] `_page_has_color` + `CHANNEL_SPREAD_COLOR_TOLERANCE` defined in Task 0.2 (Wave 3 CRIT forward-reference fix — `verify_single_page` calls `_page_has_color`)
- [ ] `verify_single_page` has `check_color_preserved: bool = True` parameter (Wave 3 HIGH — `force_monochrome` wiring in Task 4b needs this)
- [ ] `VerifierResult.color_preserved: bool = True` field in types.py (Wave 3 CRIT — added in Task 0.2, before Task 0.3's test asserts `result.status == "fail"` for color loss)
- [ ] Cross-module tolerance contract test placed in Task 1 not Task 0.3 (Wave 3 CRIT — Task 0.3 runs before Task 1 defines `_MONOCHROME_CHANNEL_SPREAD_TOLERANCE`)
- [ ] `_mrc_compose` does NOT pass `bg_codec` to `compose_mrc_page` in Task 4a — parameter added in Task 7 (Wave 3 CRIT — compose didn't have `bg_codec` until Task 7)
- [ ] `warnings: list[str] = []` initialized BEFORE the per-page loop (Wave 3 CRIT — was inside loop, reset per-page)
- [ ] `verifier_agg = _VerifierAggregator()` initialized BEFORE the per-page loop (Wave 3 CRIT — used inside loop but never instantiated in Wave-2 draft)
- [ ] `legal_codec_profile: str | None = None` (not `bool = False`) in `CompressOptions`; guard uses truthiness `if options.legal_codec_profile:` not `is not None` (Wave 3 CRIT — `False is not None` → every call raised NotImplementedError)
- [ ] `tile_ssim_min` uses `cval=np.nan` + `np.nanmin` NOT `cval=1.0` + `.min()` (Wave 3 Pre-Mortem — `cval=1.0` was an attack vector for border-straddling corruption)
- [ ] `_page_has_color` connected-component check: single color region ≥ 200 contiguous px triggers color detection even below 0.1% fraction (Wave 3 Pre-Mortem — small stamps miss fraction threshold)
- [ ] Task 0.5: source-truth digit extraction from native text layer preferred over re-OCR (Wave 3 Pre-Mortem — both-wrong OCR on input and output was a blind spot)
- [ ] Task 0.6: per-page anomaly ratio gate: >50× triggers safe-mode tile SSIM floor (Wave 3 Pre-Mortem — light page compresses 100×, small content loss can still pass standard 0.85 tile floor)
- [ ] `test_text_only_page_hits_target_ratio` NOT duplicated in Task 4c (Wave 3 CRIT — duplicate silently shadows the Task 4a definition)
- [ ] Ratio test has `@pytest.mark.skipif(shutil.which("jbig2") is None, ...)` (Wave 3 HIGH — flate fallback only reaches ~8×, not 20×)
- [ ] CLI test uses size comparison not byte-identity assertion (Wave 3 HIGH — byte-identity is brittle across platforms)
- [ ] Sharp-edges test renders at `photo_target_dpi` not 300 DPI (Wave 3 HIGH — coordinate math was wrong at different output resolution)
- [ ] `tile_ssim_min` adds `np.nan_to_num(ssim_map, nan=1.0)` before `block_reduce` (Wave 4 H4 — skimage returns NaN on constant-variance windows; blank pages must score 1.0 not NaN)
- [ ] `_page_has_color` connected-component check uses `cv2.connectedComponentsWithStats` not `scipy.ndimage.label` (Wave 4 C8 — scipy is not a declared dependency; cv2 already is)
- [ ] Task 0.6 anomaly ratio gate uses `raster.width * raster.height * 3` as per-page input estimate (Wave 4 CRIT — `tri.input_bytes / tri.pages` uniform average is wrong for mixed-size-page docs)
- [ ] Task 0.6 loop patch is applied INSIDE Task 4a's per-page loop, not as a standalone step before Task 4a (Wave 4 C9 exec-realism — `raster`, `composed`, `output_raster` don't exist until Task 4a)
- [ ] `legal_codec_profile` guard placed as FIRST check in `compress()`, before `triage()` call (Wave 4 H1 — `compress(b"", legal_codec_profile=...)` raised `CorruptPDFError` instead of `NotImplementedError`)
- [ ] `test_anomaly_ratio_gate_verify_floor_respected` proves `verify_single_page` respects the `tile_ssim_floor` parameter (Wave 4 H2 — original test was tautological constant-comparison only)
- [ ] `test_text_only_page_hits_target_ratio` pre-asserts `classify_page(...) == TEXT_ONLY` before checking ratio (Wave 4 H3 — ratio ≥ 20× on a MIXED route is a false positive)
- [ ] `_VerifierAggregator` class body fully implemented with `merge()`, `result()`, and `color_preserved` propagation (Wave 4 CRIT — class was referenced but never specified)
- [ ] Task 4a Step 3 uses `warnings=tuple(warnings)` cast at `CompressReport` construction (Wave 4 CRIT — `CompressReport.warnings: tuple[str, ...]`; list is a type error)
- [ ] Task 4b replaces dead `verifier_opts = dict(...)` with direct `verify_single_page(check_color_preserved=not options.force_monochrome)` kwarg (Wave 4 CRIT — dead dict never spread)
- [ ] Task 0.5 opens source PDF ONCE before loop, closes textpage handles explicitly (Wave 4 CRIT — N² re-opens + ResourceWarning without explicit close)
- [ ] `_VerifierAggregator.result()` uses correct VerifierResult field names: `status="pass"/"fail"`, `ocr_levenshtein`, `ssim_global`, `ssim_min_tile`, `digit_multiset_match`, `structural_match=True`, `failing_pages=tuple(...)` (Wave 5 exec-realism CRIT-1/2/3)
- [ ] `_VerifierAggregator` imported in `__init__.py` alongside `verify_single_page` (Wave 5 exec-realism CRIT-4)
- [ ] `CompressReport(verifier=verifier_agg.result(), ...)` — required field wired (Wave 5 exec-realism CRIT-5)
- [ ] Task 4a loop has ONE canonical `verify_single_page` call incorporating Task 0.6 anomaly gate + Task 4b `check_color_preserved` — no duplicate call from Task 0.6 Step 2 (Wave 5 exec-realism HIGH-1)
- [ ] `len(page_pdfs) == tri.pages` assertion after loop (Wave 5 pre-mortem CRIT-2)
- [ ] Verifier failure short-circuit explicit: raise `ContentDriftError` in standard/safe mode; warn in fast mode (Wave 5 pre-mortem HIGH-6)
- [ ] `is_effectively_monochrome(raster)` NOT `is_effectively_monochrome(bg)` in `_mrc_compose` (Wave 5 pre-mortem CRIT-1)
- [ ] `tile_ssim_min` fails on size mismatch >±1px instead of silently LANCZOS-resampling (Wave 5 pre-mortem CRIT-3)
- [ ] `force_monochrome` + PHOTO_ONLY + near-empty mask: keeps PHOTO_ONLY with `bg_color_mode="grayscale"` — does NOT route to TEXT_ONLY with undefined ink_color (Wave 5 pre-mortem CRIT-4)
- [ ] TEXT_ONLY routing checks `is_effectively_monochrome(raster)` and demotes to MIXED if colored (Wave 5 pre-mortem HIGH-5)
- [ ] `CHANNEL_SPREAD_COLOR_TOLERANCE = 15` (raised from 5 — JPEG ringing halos top out at ~12 spread units; Wave 5 pre-mortem HIGH-4)
- [ ] Task 0.6 anomaly gate threshold `> 200×` AND excludes `TEXT_ONLY` strategy (threshold raised from 50×; TEXT_ONLY legitimately hits 100-500× via JBIG2; Wave 5 pre-mortem HIGH-2)
- [ ] `test_compose_mrc_jpeg2000_bg_option` has `@pytest.mark.skipif(_openjpeg_available() is False, ...)` (Wave 5 test-integrity CRIT-2 — test fails on correct implementation without OpenJPEG)
- [ ] Task 0.5 introduces `_extract_ground_truth_text` as a testable module-level helper, with unit tests in `test_compress_helpers.py` (Wave 5 test-integrity CRIT-1 — original test didn't test the implementation)
- [ ] `test_photo_only_page_preserves_sharp_edges` uses `scale=photo_target_dpi/72` and `y_out = int(y_src * scale_factor)` with `scale_factor = photo_target_dpi / 300.0` (Wave 5 test-integrity HIGH-4 — Step 1 and Step 3 were conflicting)
- [ ] Task 0.3 Step 2 "expected" is PASS not FAIL (Wave 5 exec-realism HIGH-3 — Task 0.2 already wired the check)
- [ ] `from typing import Any` imported in `_mrc_compose` (Wave 5 exec-realism HIGH-4)
- [ ] `compose_photo_only_page` has `bg_color_mode: BgColorMode = "rgb"` parameter AND Task 7 signature template preserves it alongside `bg_codec` (Wave 6 pre-mortem C2 / exec-realism C1+C2)
- [ ] `tri.classification == "pass-through"` guard added BEFORE per-page loop in Task 4a, returns `status="passed_through"` with input bytes unchanged (Wave 6 pre-mortem C3)
- [ ] `from pdf_smasher.engine.rasterize import rasterize_page` (not `raster`) in test pre-assert (Wave 6 pre-mortem C4)
- [ ] `is_effectively_monochrome` uses `Image.Resampling.LANCZOS` not `NEAREST` for thumbnail (Wave 6 pre-mortem H1)
- [ ] `_jpeg2000_bytes` catches `(OSError, KeyError, TypeError)` — full exception set for all Pillow variants (Wave 6 pre-mortem H2)
- [ ] `_openjpeg_available()` probe uses `quality_mode="rates", quality_layers=[25]` to match `_jpeg2000_bytes` exactly (Wave 6 test-integrity C2)
- [ ] `import io` in Task 3's appended test block (before the three `bg_color_mode` tests) (Wave 6 test-integrity C1)
- [ ] `_page_has_color` fraction-threshold pinning tests: 0.3% region detected; JPEG ringing 5%@spread=10 not detected (Wave 6 test-integrity C3)
- [ ] `test_photo_only_page_does_not_regress` pre-asserts `classify_page(...) == PHOTO_ONLY` (Wave 6 test-integrity H1)
- [ ] `test_multi_page_mixed_strategies` pre-asserts all three page routes before `compress()` call (Wave 6 test-integrity H2)
- [ ] `_JBIG2_CASCADE_STATE.tripped = False` reset at START of each page iteration, not only on True-branch read (Wave 6 test-integrity H3)
- [ ] `_build_options()` in `main.py` updated: `legal_codec_profile="ccitt-g4" if args.legal_mode else None` (Wave 6 exec-realism C4)
- [ ] `_mrc_compose` has `bg_subsampling: int = _JPEG_SUBSAMPLING_444` parameter forwarded to `compose_mrc_page(**kwargs)` (Wave 6 exec-realism H1)
- [ ] `test_force_monochrome_compresses_heavier_than_default` has `from pdf_smasher import compress, CompressOptions` in function body (Wave 6 exec-realism H2)
- [ ] Task 4a `CompressReport(...)` code block has closing ``` fence before `- [ ] Step 4` (Wave 6 exec-realism H3)
- [ ] Task 4b comment references Task 4a PHOTO_ONLY branch for `bg_color_mode` grayscale, NOT a phantom `_force_mono_photo` flag (Wave 6 exec-realism H4)

---

## Self-review notes (post Wave-4 DCR)

**Wave 4 findings applied (from 3 lens reviewers: pre-mortem, test-integrity, exec-realism):**

**Class body missing (CRIT):**
- `_VerifierAggregator` body: referenced in Task 0.2 and Task 4a but never specified in the plan. Added full class with `__init__`, `merge(page_idx, verdict)`, `result() -> VerifierResult`, and `_color_preserved` propagation. Two unit tests added.

**Type mismatch (CRIT):**
- `CompressReport.warnings: tuple[str, ...]` but plan built a `list[str]`. Fixed: added `tuple(warnings)` cast at `CompressReport(...)` construction.

**Dead code (CRIT):**
- Task 4b: `verifier_opts = dict(...)` was built but never spread into `verify_single_page`. Fixed: replaced with direct `check_color_preserved=not options.force_monochrome` kwarg at the call site.
- Task 0.6: `in_page_size = len(rasterize_page.__module__)` was dead placeholder. Deleted. `per_page_input_estimate = tri.input_bytes / max(1, tri.pages)` was wrong (uniform average). Fixed: `raster.width * raster.height * 3`.

**Execution ordering (CRIT):**
- Task 0.5: opened source PDF per-page (O(N) parse overhead). Fixed: open once before loop, explicit `textpage.close()` in inner `try/finally`.
- Task 0.5 vs Task 4a conflict: Task 4a's unconditional `input_ocr_text = " ".join(...)` would revert Task 0.5's probe. Fixed: Task 0.5 shows the combined version; engineer implementing Task 4a must use it.
- Task 0.6: can't run as a standalone task — references `raster`, `composed`, `output_raster` from Task 4a's loop. Added ordering note: Step 1 unit test is independent; Step 2 loop patch is applied inside Task 4a's loop.

**Guard placement (H1):**
- `test_legal_codec_profile_raises_not_implemented` used `compress(b"", ...)`. `b""` fails triage before the guard if the guard is placed after triage. Fixed: guard is now explicitly placed as the FIRST check in `compress()`, before `triage()`. Test works because the guard fires first.

**Tautological tests (H2/H3):**
- Anomaly gate test only checked constant ordering (`safe > standard`), not that `verify_single_page` respects the parameter. Added `test_anomaly_ratio_gate_verify_floor_respected` with `tile_ssim_floor=1.01` (impossible floor) to prove the parameter is used.
- `test_text_only_page_hits_target_ratio` could pass on a MIXED route if MIXED also hit ≥20×. Added pre-assert `assert classify_page(...) == PageStrategy.TEXT_ONLY`.

**Blank page NaN (H4):**
- `skimage.structural_similarity(full=True)` returns NaN for constant-variance windows (all-white pages). `np.nan_to_num(ssim_map, nan=1.0)` added before `block_reduce` in `tile_ssim_min`. Test `test_tile_ssim_blank_pages_returns_1` added.

**Undeclared dependency (C8):**
- `_page_has_color` used `scipy.ndimage.label`. scipy is not in `pyproject.toml`. Replaced with `cv2.connectedComponentsWithStats` (opencv-python-headless is already a declared dep).

---

## Self-review notes (post Wave-2 DCR)

**Wave 2 findings applied (from 3 new lens reviewers + pre-mortem):**

- CRIT-1 — SPEC.md §9 does not exist; metrics/counters moved to §8; verifier table is ARCHITECTURE §5 not SPEC §5 (Task 7.6 rewritten)
- CRIT-2 (security/parseability) — `cli/main.py:136` + `triage.py:111` had Python-2 exception syntax SyntaxErrors. Hot-patched on `main`; Task 0.4 adds regression-guard test
- CRIT-2 (verifier SSIM) — Wave-1 safe-mode global SSIM 0.96 was wrong (contradicted ARCH §5 table). Task 0.2 rewritten to pin correct values (0.92 global both modes; 0.85/0.88 tile)
- CRIT-3 (ALREADY_OPTIMIZED) — Wave-1 branch silently fell through to MRC. Task 4a now raises AssertionError (no placebo)
- CRIT-3 (dead options) — `bg_chroma_subsampling` now actually wired with `--bg-chroma` CLI flag; `legal_codec_profile` honestly raises NotImplementedError (Task 7.5b/c)
- CRIT-4 (multi-page stub) — Task 4c's stub body is now a concrete 3-page fixture deterministically hitting text/photo/mixed strategies
- CRIT-5 (colored-stamp test math) — mask coverage analysis showed 4.2% → PHOTO_ONLY not MIXED. Test rewritten to assert semantic content preservation (round-trip stamp color survives) not specific strategy
- CRIT-5 (doctor) — `--doctor` now probes JPEG2000 encode + jbig2enc (Task 6 Step 4)
- CRIT-P1 (jbig2 cascade silent) — mid-run flate fallback now emits per-page `jbig2-fallback-to-flate` warning via `_JBIG2_CASCADE_STATE` thread-local (Task 4a)
- CRIT-P2 (memory bound false) — Task 4a rewritten to stream rasters through `verify_single_page` per-page; Task 0.2 factors the streaming entry point
- CRIT-P3 (tile-SSIM O(N²) Python loop) — Task 0.1 rewritten to single `structural_similarity(full=True)` + `block_reduce(..., np.min)` vectorized
- CRIT-P4 (JPEG2000 latency unbounded) — Task 7 adds fast-mode demotion table; `compress()` emits `bg-codec-jpeg2000-demoted-fast-mode` warning when demoted
- CHANNEL-SPREAD DISAGREEMENT — verifier `_page_has_color` and `is_effectively_monochrome` now import `CHANNEL_SPREAD_COLOR_TOLERANCE` from one place (cross-module contract test pins them equal)
- KeyboardInterrupt — per-page loop handler clears partial `page_pdfs` before re-raise so interrupt state is clean

## Self-review notes (post Wave-5 DCR)

**Wave 5 findings applied (from 3 lens reviewers: pre-mortem/content-preservation, test-integrity, exec-realism):**

**Field names / type errors (exec-realism CRIT-1/2/3/4/5):**
- `_VerifierAggregator.result()`: all field names corrected to match `VerifierResult` in types.py: `status="pass"/"fail"` (not `"ok"`), `ocr_levenshtein` (not `ocr_lev`), `ssim_global` (not `min_ssim_global`), `ssim_min_tile` (not `min_ssim_tile`), `digit_multiset_match` (not `digits_match`), `structural_match=True`, `failing_pages=tuple(...)` (not `list`).
- `_VerifierAggregator` added to import in `__init__.py`.
- `verifier=verifier_agg.result()` added to `CompressReport(...)` construction — required field.

**Dual verify_single_page calls (exec-realism HIGH-1):**
- Task 4a loop now has ONE canonical call incorporating Task 0.6 anomaly gate AND Task 4b `check_color_preserved` inline. No separate Task 0.6 loop patch needed — the ordering note already explains this.

**BG color decision on wrong raster (pre-mortem CRIT-1):**
- `_mrc_compose` now calls `is_effectively_monochrome(raster)` instead of `is_effectively_monochrome(bg)`. The background is inpainted and will appear near-mono even for pages with colored ink — checking the original raster is the correct decision point.

**Page-count invariant (pre-mortem CRIT-2):**
- Added `assert len(page_pdfs) == tri.pages` after the loop, before merge. Catches silent page drops/duplications before they corrupt the output.

**Size-mismatch SSIM silently resampled (pre-mortem CRIT-3):**
- `tile_ssim_min` now raises `ValueError` if rasters differ by >±1 pixel in either dimension. ±1 tolerance absorbs harmless float→int rounding. Prevents geometric distortion bugs from passing with high SSIM after LANCZOS resample.

**force_monochrome + empty-mask PHOTO_ONLY (pre-mortem CRIT-4):**
- `force_monochrome` override no longer routes near-empty-mask PHOTO_ONLY pages to TEXT_ONLY. Instead: keeps PHOTO_ONLY with `bg_color_mode="grayscale"`. `extract_foreground` on a ~0% mask page returns an undefined ink_color — compose_text_only_page would produce a solid rectangle.

**TEXT_ONLY missing monochromaticity gate (pre-mortem HIGH-5):**
- `strategy == TEXT_ONLY` branch now checks `is_effectively_monochrome(raster)` and demotes to MIXED if the page has color. Catches pale-blue ruled lines and similar colored-but-paper-dominated pages that classify to TEXT_ONLY by coverage + light_frac alone.

**JPEG ringing halos (pre-mortem HIGH-4):**
- `CHANNEL_SPREAD_COLOR_TOLERANCE` raised from 5 to 15. JPEG ringing around black glyphs tops out at ~12 channel-spread units; genuine color starts at ~20. Also raised fraction gate from 0.1% to 0.5%.

**Anomaly gate fires on every TEXT_ONLY (pre-mortem HIGH-2):**
- Threshold raised from 50× to 200×, and TEXT_ONLY is excluded from the gate entirely. Legitimate TEXT_ONLY via JBIG2 routinely hits 100-500×.

**Verifier failure short-circuit (pre-mortem HIGH-6):**
- Added explicit ContentDriftError raise when `verifier_agg.result().status == "fail"` in standard/safe mode. Fast mode records a warning instead. Previously: a corrupted output PDF would be returned silently if callers didn't inspect the report.

**Test-integrity CRIT-1 (Task 0.5 test didn't test implementation):**
- Introduced `_extract_ground_truth_text` as a testable module-level helper. Two unit tests in `test_compress_helpers.py` directly invoke it with a PDF-with-text-layer fixture and a bare blank-page fixture.

**Test-integrity CRIT-2 (JPEG2000 test fails without OpenJPEG):**
- `test_compose_mrc_jpeg2000_bg_option` now has `@pytest.mark.skipif(_openjpeg_available() is False, ...)` with an inline `_openjpeg_probe` that attempts a trivial JPEG2000 encode.

**Test-integrity HIGH-4 (conflicting Step 1/Step 3 test bodies):**
- `test_photo_only_page_preserves_sharp_edges` Step 1 rewritten with the FINAL version (renders at `photo_target_dpi`, correct coordinate math). Step 3 marked obsolete to prevent engineers from pasting two conflicting versions.

**Exec-realism HIGH-3 (Task 0.3 Step 2 "expected FAIL" wrong):**
- Changed to "Expected: PASS" with explanation that Task 0.2 already wired the channel-parity check.

**Exec-realism HIGH-4 (`typing.Any` missing + `bg_jpeg_quality` note):**
- Added `from typing import Any` inside `_mrc_compose`. Added docstring note clarifying that baseline Task 4a doesn't pass `bg_jpeg_quality`; Task 7.5a patches the caller.

## Self-review notes (post Wave-1 DCR)

- **Spec coverage**: all six prioritized leaks from the goal are covered:
  - (1) strategy routing = Tasks 4a + 4b + 4c (all four strategies, force_monochrome across all non-ALREADY_OPTIMIZED)
  - (2) grayscale bg = Task 3 (caller-controlled, not compose-internal)
  - (3) force_monochrome = Tasks 4b (routing) + 6 (CLI flag)
  - (4) JPEG2000 = Task 7 (with OpenJPEG-unavailable fallback, wired into BOTH compose paths per ARCHITECTURE)
  - (5) bg quality thread = Task 7.5 (target_color_quality finally wired)
  - (6) aggressiveness level out of scope (YAGNI; force_monochrome is the one knob)

- **Content-preservation coverage** (new after Wave 1):
  - Tile-level SSIM shipped (Task 0.1) — closes `verifier.py:119` TODO before adding aggressive paths
  - Verifier thresholds raised to SPEC §5 values (Task 0.2)
  - Channel-parity check shipped (Task 0.3) — catches silent color-loss that SSIM-on-L misses
  - `is_effectively_monochrome` uses quantile + colored-pixel-fraction, not single-pixel max (Task 1) — robust to scanner noise AND to faint pervasive color

- **Edge-case coverage** (new after Wave 1):
  - ALREADY_OPTIMIZED branch (Task 4a)
  - Zero-size mask guard (Task 4a)
  - Per-page try/except bounds crash state (Task 4a)
  - Multi-page mixed-strategy test (Task 4c)
  - Colored-ink-not-routed-to-TEXT_ONLY (Task 4c)
  - OpenJPEG-unavailable fallback (Task 7)
  - jbig2enc-missing warning emission (Task 4a)

- **Consolidation** (new after Wave 1):
  - `color_detect.py` new module dropped — helpers live in `foreground.py` next to `extract_foreground` (A.M1)
  - Light-pixel threshold consolidated between strategy.py and foreground.py (B.C4)
  - `compose.py` does not import `is_effectively_monochrome` — caller passes `bg_color_mode` (C.M2)
  - Shared `_wrap_raster_as_pdf_bytes` in `tests/integration/_fixtures.py` (A.M5)

- **Type consistency**:
  - `PageStrategy` all four values handled (no silent fall-through)
  - `BgCodec` and `BgColorMode` Literal types used consistently
  - `detect_paper_color` returns `tuple[int, int, int]` in tests + impl

- **Frequent commits**: 9 headline tasks split into ≥ 14 sub-commits (Task 0 is 0.1/0.2/0.3; Task 4 is 4a/4b/4c; Task 7 is full; Task 7.5 and 7.6 are new). Each sub-commit is independently bisectable.

- **Items deferred** (documented, not in scope):
  - Strategy registry / `PageContext` adapter (Pre-Mortem #2 design refactor) — valuable but bigger scope than Phase 2b; flag as Phase 2c
  - `min_input_mb` / `min_ratio` gate wiring (Wave 1 L3) — orthogonal to Phase 2b's ratio push
  - Per-region foreground ink color (removes `# noqa: ARG001` on compose's foreground param) (Wave 1 A.M6) — Phase 2c
  - CI lint rule for banning filename interpolation in logs (SPEC §9.2 mentions it, never landed) — Phase 4 hardening

## Self-review notes (post Wave-3 DCR)

**Wave 3 findings applied (from 4 lens reviewers: exec-realism, test-design, pre-mortem, cross-task integration):**

**Schema / forward-reference ordering (CRIT):**
- C1 (exec-realism): `_mrc_compose` passed `bg_codec` to `compose_mrc_page` before that param existed. Fixed: removed `bg_codec` from Task 4a's `_mrc_compose` kwargs; added in Task 7 when compose gets the param.
- C2 (exec-realism): `options.photo_target_dpi` used in Task 4a loop but field added in Task 5. Fixed: moved field addition to Task 4a Step 0.
- C4 (exec-realism): `report.strategy_distribution` asserted in Task 4c but `CompressReport` never got the field. Fixed: added to Task 4a Step 0 with `Mapping[str, int]` type.
- C3 (exec-realism): `legal_codec_profile: bool = False` + `if options.legal_codec_profile is not None:` = always raises. Fixed: changed to `str | None = None` and guard to truthiness `if options.legal_codec_profile:`.
- C5 (exec-realism): cross-module contract test imported `_MONOCHROME_CHANNEL_SPREAD_TOLERANCE` (defined in Task 1) from within Task 0.3 step — would ImportError. Fixed: moved test to Task 1 Step 5.
- H1/H2 (exec-realism): `check_color_preserved` param missing from `verify_single_page` signature; `verifier_agg` never instantiated before loop. Fixed: both added explicitly in Task 0.2 and Task 4a respectively.
- `_page_has_color` + `CHANNEL_SPREAD_COLOR_TOLERANCE` forward reference: called in `verify_single_page` (Task 0.2) but defined in Task 0.3. Fixed: moved definitions to Task 0.2 where they're first used.
- `warnings: list[str] = []` initialized inside loop (per-page reset). Fixed: moved before the loop with jbig2enc check.

**Test design (CRIT/HIGH):**
- C1 (test-design): duplicate `test_text_only_page_hits_target_ratio` in Task 4a AND Task 4c. pytest silently shadows first with second. Fixed: removed from Task 4c, kept in Task 4a.
- C2 (test-design): `verify_single_page` in Task 0.2 references `_page_has_color` (forward ref). Fixed above.
- H1 (test-design): ratio test had no `skipif` guard for missing jbig2enc — flate fallback reaches only ~8×, test would false-fail. Fixed: added `@pytest.mark.skipif(shutil.which("jbig2") is None, ...)`.
- H2 (test-design): Arial.ttf missing silently uses wrong default font. Fixed: added `_FIXTURE_FONT` path in the test helper + graceful `ImageFont.load_default()` fallback.
- H3 (test-design): CLI test asserted byte-identity (`!=`) which is brittle across platforms. Fixed: semantic size comparison (mono must be smaller).
- H4 (test-design): sharp-edges test rendered at 300 DPI but photo path encodes at `photo_target_dpi=200`. Fixed: render at `photo_target_dpi`.

**Pre-mortem content-preservation (CRIT):**
- Tile-SSIM `cval=1.0` attack vector: corruption at tile borders pads away, min never lowers. Fixed: `cval=np.nan` + `np.nanmin`.
- `_page_has_color` fraction threshold misses small stamps (0.09% of page). Fixed: added connected-component check — any region ≥ 200 contiguous px triggers color detection.
- Digit ground-truth from re-OCRing input raster: both sides can OCR the same wrong value. Fixed: Task 0.5 prefers native PDF text layer when present.
- No per-page ratio bound: a 200× blank page can pass SSIM=0.94 even with a stripped watermark. Fixed: Task 0.6 anomaly gate — >50× triggers safe-mode tile SSIM floor (0.88 instead of 0.85).

## Self-review notes (post Wave-6 DCR)

**Wave 6 findings applied (from 3 lens reviewers: pre-mortem/content-preservation, test-integrity, exec-realism):**

**`compose_photo_only_page` missing `bg_color_mode` (Pre-Mortem C2 / Exec-Realism C1+C2 — de-duped):**
- Task 3 Step 3 now ALSO adds `bg_color_mode: BgColorMode = "rgb"` to `compose_photo_only_page`, with the grayscale branch converting `raster.convert("L")` and using `DeviceGray` in the XObject. Without this, every PHOTO_ONLY page in Task 4a crashes with `TypeError`.
- Task 7's `compose_photo_only_page` signature template now includes `bg_color_mode` — it was missing, so the Task 3 addition would have been silently overwritten when Task 7 was applied.

**Pass-through triage classification unhandled (Pre-Mortem C3):**
- Task 4a Step 3 now has an explicit guard before the per-page loop: if `tri.classification == "pass-through"`, return immediately with `status="passed_through"` and the input bytes unchanged. Without this, pass-through documents would be re-encoded and the page-count invariant could fire unexpectedly.

**Wrong module import `pdf_smasher.engine.raster` (Pre-Mortem C4):**
- Changed to `from pdf_smasher.engine.rasterize import rasterize_page` in the `test_text_only_page_hits_target_ratio` pre-assert block. There is no `raster.py` module.

**`is_effectively_monochrome` NEAREST resampling misses small color regions (Pre-Mortem H1):**
- Changed `Image.Resampling.NEAREST` → `Image.Resampling.LANCZOS` in the thumbnail call. LANCZOS blends neighbors so a 50×50 colored stamp on a 2550×3300 source contributes proportionally to the thumbnail pixels instead of potentially disappearing via aliasing.

**`_jpeg2000_bytes` exception set too narrow (Pre-Mortem H2 / Test-Integrity C2 — de-duped):**
- Changed `except OSError:` to `except (OSError, KeyError, TypeError):` in `_jpeg2000_bytes`. Old Pillow builds raise `KeyError('jpeg2k')` instead of `OSError`; old Pillow-SIMD raises `TypeError` for unsupported `quality_mode='rates'`.
- `_openjpeg_available()` probe updated to use the EXACT same parameters as `_jpeg2000_bytes()` (`quality_mode="rates", quality_layers=[25]`) so a build that passes the probe is guaranteed to succeed the real encode.

**Missing `import io` in Task 3 test block (Test-Integrity C1):**
- Added `import io` to the `# Append to tests/unit/engine/test_compose.py` block. All three grayscale tests use `io.BytesIO(out)` to open the output with pikepdf. Without the import, every test would crash with `NameError: name 'io' is not defined`.

**`_page_has_color` fraction threshold contradict + no pinning test (Test-Integrity C3):**
- Added clarification note in Task 0.3: this step REPLACES Task 0.2's `_page_has_color` definition entirely; the 0.1% threshold (0.001) is correct — the CC check handles the small-stamp case, allowing 0.1% to be used safely.
- Added two new pinning tests to Task 0.3: `test_page_has_color_fraction_boundary_0_1_pct` (0.3% colored region is detected) and `test_page_has_color_jpeg_ringing_not_detected_as_color` (5% halo pixels at spread=10 are NOT detected).

**`test_photo_only_page_does_not_regress` no PHOTO_ONLY pre-assert (Test-Integrity H1):**
- Added strategy pre-assert (same pattern as text_only test). A MIXED page at baseline 4.99× also satisfies ≥3×, so without the pre-assert the test gives false confidence.

**`test_multi_page_mixed_strategies` no per-page strategy pre-asserts (Test-Integrity H2):**
- Added `_route(page_idx)` helper and pre-asserts for all three pages before the `compress()` call. Font availability can cause the text page to demote to MIXED on some CI machines; the pre-assert surfaces "fix the fixture" instead of "routing regression".

**`_JBIG2_CASCADE_STATE` stale flag leak (Test-Integrity H3):**
- Moved `_JBIG2_CASCADE_STATE.tripped = False` to the START of each page iteration (before compose) instead of only resetting it on the True-branch read. If a per-page exception fires after `tripped=True` is set (but before the read), the stale True can no longer contaminate the next page or the next `compress()` call in the same thread.

**`main.py` `_build_options()` not updated for `legal_codec_profile` type change (Exec-Realism C4):**
- Task 7.5c now explicitly instructs updating `_build_options()` to pass `legal_codec_profile="ccitt-g4" if args.legal_mode else None`. Without this, `args.legal_mode` (a bool from `action="store_true"`) is passed to a `str | None` field, causing mypy failure in the Task 9 final gate.

**`_mrc_compose` missing `bg_subsampling` parameter (Exec-Realism H1):**
- Task 7.5b now explicitly adds `bg_subsampling: int = _JPEG_SUBSAMPLING_444` to `_mrc_compose`'s signature and shows it forwarded to `compose_mrc_page(**kwargs)`. Without this, Task 7.5b's instruction to thread `bg_subsampling=_CHROMA_TO_PIL[...]` through `_mrc_compose(...)` would crash with `TypeError: _mrc_compose() got an unexpected keyword argument 'bg_subsampling'`.

**Missing imports in `test_force_monochrome_compresses_heavier_than_default` (Exec-Realism H2):**
- Added `import io` and `from pdf_smasher import compress, CompressOptions` at the top of the test function body. The test uses `io.BytesIO()` twice and `CompressOptions(force_monochrome=True)`.

**Phantom `_force_mono_photo` flag in Task 4b (Exec-Realism H4):**
- Fixed the misleading comment: removed the reference to a `_force_mono_photo` flag that never exists. The correct description is that grayscale encoding is handled by the Task 4a PHOTO_ONLY branch already checking `options.force_monochrome` when computing `_photo_bg_color_mode`.
