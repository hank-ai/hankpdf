# Per-Page Selective MRC — Design

**Date:** 2026-04-27
**Author:** Jack Neil (with implementation drafted by Claude)
**Goal:** Skip the MRC compression pipeline on pages that won't benefit, copying them verbatim from the input. On native-export PDFs every page is verbatim → output equals input → wall time drops to <1s. On mixed decks only the photo-heavy pages run the expensive pipeline. On true scans every page MRCs (no change from today).

## 1. Context

The 31-PDF benchmark matrix in `docs/PERFORMANCE.md` showed that 30 of 31 native-export presentation PDFs passthrough at default — the post-hoc `--min-ratio 1.5` gate correctly throws the MRC output away because the pipeline can't beat already-efficient native PDF compression. But the pipeline still ran on every page first: rasterize + classify + compose + verify, several seconds per file, output discarded.

The codebase already had the design intent partly in place:

- `pdf_smasher/engine/strategy.py` defines `PageStrategy.ALREADY_OPTIMIZED` with a docstring noting "Detected via triage, not this module."
- `pdf_smasher/__init__.py:506` raises `AssertionError("compress() has no handler for this value")` if it ever sees that strategy.

In other words: `ALREADY_OPTIMIZED` was reserved as the per-page passthrough class but neither the detector nor the handler was ever written. This spec finishes the job.

The user's framing: "at a page level, if we think you'll have meaningful results by compressing, do it. If not, skip that page." Whole-document passthrough is then the degenerate case where every page is skipped.

## 2. Goals & Non-Goals

**Goals:**

1. Per-page binary classification (MRC-worthy vs verbatim-copy) using a cheap signal (no rasterizing, no rendering).
2. Verbatim-copy pages reach the merge stage as 1-page PDF slices identical to the input — bytes, fonts, text layer, all preserved.
3. The whole-doc native-PDF case (every page verbatim) returns the input unchanged at top-level, before spinning up workers — wall time goes from 3-30s to <1s.
4. The signal threshold is tunable via CLI flag and `CompressOptions` field.
5. CompressReport records which pages were skipped (debugging, sidecar manifests).
6. New flags from PR #11 (`--re-ocr`, `--strip-text-layer`) compose cleanly with the new gate.
7. No regression on true-scan inputs: every page still MRCs at default thresholds.

**Non-Goals:**

1. Per-page strategy selection BEYOND binary MRC-vs-verbatim. The existing `PageStrategy.{TEXT_ONLY, PHOTO_ONLY, MIXED}` enum keeps owning the within-MRC routing decisions.
2. Image-area heuristic (parse the content stream's `Do` operators and multiply by XObject geometry). Image-byte-fraction is the v1 signal; image-area is potential future tuning.
3. Configurable per-page strategies via CLI ("force page 5 through MRC, skip page 7"). YAGNI.
4. Removing the existing `--min-ratio 1.5` post-hoc gate. That stays as belt-and-suspenders against pages where the per-page gate said "MRC" but the resulting output was still bigger.
5. Per-page TEXT-layer policy decisions. Text layer handling is already feature-complete in PR #11.

## 3. Architecture

### 3.1 Detector — `score_pages_for_mrc(pdf_bytes) -> list[bool]`

New module: `pdf_smasher/engine/page_classifier.py`. Single function, single signal:

```python
def score_pages_for_mrc(
    pdf_bytes: bytes,
    *,
    password: str | None = None,
    min_image_byte_fraction: float = 0.30,
) -> list[bool]:
    """Return one bool per page: True = MRC-worthy, False = verbatim-copy.

    Walks the input PDF once via pikepdf, computes per-page
    image_xobject_bytes / page_total_byte_budget, and returns True for
    pages whose ratio is at or above the threshold. No decoding, no
    rendering, no Tesseract — just stream-length inspection.
    """
```

**Signal.** Per page:

- `image_bytes`: sum of `/Length` for every `/XObject /Image` referenced from this page's `/Resources/XObject` dictionary. Includes only images on this specific page (not document-wide totals).
- `page_byte_budget`: `len(content_stream_bytes) + image_bytes + sum(other_xobject_bytes)` for the page, where `other_xobject_bytes` includes /Form XObjects (vector subforms) and any other non-image XObject. Uses the actual on-disk encoded stream lengths — a fair denominator for "how much of this page is image data."

A page is MRC-worthy if `image_bytes / page_byte_budget >= min_image_byte_fraction`. The default `0.30` gives clean separation on the matrix data (native PDFs sit at 0-15%; scan-derived pages sit at 70-95%).

**Defensive defaults.** If a page can't be analyzed (corrupt stream, exception), default to `True` (MRC-worthy) — fail-safe to today's behavior. Logged as a warning code.

### 3.2 Whole-doc shortcut

In `compress()`, after triage and before worker dispatch:

```python
mrc_flags = score_pages_for_mrc(input_data, password=options.password,
                                  min_image_byte_fraction=options.min_image_byte_fraction)
if not any(mrc_flags) and not options.re_ocr and not options.strip_text_layer:
    # Every page is verbatim AND no flag forces full pipeline → return input unchanged.
    return _build_passthrough_report(
        input_data, pages=tri.pages, wall_ms=...,
        reason="no page meets the image-content threshold",
        warning_code="passthrough-no-image-content",
    )
```

Reuses the existing `_build_passthrough_report` helper. Exit code: `EXIT_NOOP_PASSTHROUGH = 2` (existing). The new warning code goes into the `report.warnings` tuple.

### 3.3 Per-page worker — verbatim-copy fast path

`_WorkerInput` gains a new field `mrc_worthy: bool`. When `False`, the worker:

1. Skips rasterize / mask / classify / compose / verify entirely.
2. Returns the existing `winput.input_page_pdf` (the 1-page slice already produced by `compress()`'s split stage) as the `composed_bytes` field of `_PageResult`.
3. Sets `strategy_name = "already_optimized"` — the string value of the existing `PageStrategy.ALREADY_OPTIMIZED` enum, so the strategy taxonomy stays in sync between `engine/strategy.py` and the per-page result.
4. Synthesizes a trivially-passing `PageVerdict` using the same shape as the existing `skip_verify` path in `pdf_smasher/__init__.py` (around the line that constructs `_PageVerdict(page_index=-1, passed=True, lev=1.0, ssim_global=0.0, ssim_tile_min=0.0, digits_match=False, color_preserved=False)`). The merge stage requires a verdict object on every per-page result.
5. Reports per-page bytes: input == output, ratio = 1.0.

The merge stage is unchanged — it already accepts per-page composed PDF bytes regardless of how they were produced.

### 3.4 Force-MRC flags

`--re-ocr` and `--strip-text-layer` (introduced in PR #11) bypass the gate entirely:

- `--re-ocr` requires Tesseract on every page; that requires rasterization; verbatim copy is incompatible.
- `--strip-text-layer` requires producing output with no text layer; verbatim copy preserves the input text layer; incompatible.

Both flags effectively set `min_image_byte_fraction = 0.0` for the run (every page is MRC-worthy). Documented in CompressOptions docstring + CLI help.

### 3.5 New `PageStrategy.ALREADY_OPTIMIZED` handler

The existing `AssertionError` at `pdf_smasher/__init__.py:506` becomes the verbatim-copy fast path. The strategy enum value moves from "reserved for future use" to "in production."

`engine/strategy.py:classify_page` is unchanged — it still returns one of `TEXT_ONLY / PHOTO_ONLY / MIXED`. The new `ALREADY_OPTIMIZED` decision happens upstream in `score_pages_for_mrc` and is communicated to the worker via `_WorkerInput.mrc_worthy=False`. The worker MUST check `mrc_worthy` BEFORE rasterizing or calling `classify_page`. When `mrc_worthy=False` the worker emits a `_PageResult` with `strategy_name="already_optimized"` (the string form of `PageStrategy.ALREADY_OPTIMIZED`).

## 4. Public API surface

### CompressOptions

```python
@dataclass(frozen=True)
class CompressOptions:
    ...
    # Per-page MRC gate (see docs/superpowers/specs/2026-04-27-per-page-selective-mrc-design.md).
    # Pages whose image_xobject_bytes / page_total_bytes is below this
    # threshold are copied verbatim from the input — no rasterize, no
    # MRC, no Tesseract. Set to 0.0 to disable the gate (force every
    # page through the MRC pipeline). The flags --re-ocr and
    # --strip-text-layer also disable the gate (every page MRCs).
    min_image_byte_fraction: float = 0.30
```

### CLI flag

```
--per-page-min-image-fraction 0.30
    Per-page gate: pages with image bytes below this fraction of the
    page's byte budget are copied verbatim instead of recompressed.
    Default 0.30 (catches the common "native PDF, no scan content" case
    and skips it). Set to 0.0 to force the full MRC pipeline on every
    page.
```

### CompressReport

```python
@dataclass(frozen=True)
class CompressReport:
    ...
    # 0-indexed page numbers that the per-page gate copied verbatim
    # from the input rather than running through the MRC pipeline.
    # Empty tuple = every page was MRC'd (true-scan case) OR a
    # whole-doc passthrough fired (in which case `status="passed_through"`).
    pages_skipped_verbatim: tuple[int, ...] = ()
```

### Warning codes

- `passthrough-no-image-content` — whole-doc passthrough; every page below the threshold.
- `pages-skipped-verbatim:N` — N pages were copied verbatim within an otherwise-MRC'd run. (Aggregate count, not per-page indices, to keep warning lines bounded.)

## 5. Test Strategy

### Unit tests (synthetic fixtures)

- `tests/unit/engine/test_page_classifier.py`:
  - Pure-text PDF (no images) → `[False]` for every page.
  - Pure-image PDF (one big image XObject per page) → `[True]` for every page.
  - Mixed: 1 text page + 1 photo page → `[False, True]`.
  - Threshold edge cases: page at exactly 0.30, 0.299, 0.301.
  - Defensive default: corrupt page → `True` (fail-safe).

### Integration tests

- `tests/unit/test_compress_per_page_gate.py`:
  - 2-page PDF (1 photo, 1 text). `compress()` output has 2 pages. Page 0 (photo) → MRC-compressed (output bytes != input bytes). Page 1 (text) → verbatim (output bytes == input bytes for that page). `pages_skipped_verbatim == (1,)`.
  - 100%-native PDF (e.g., the existing smoke fixture or a synthetic) → whole-doc passthrough. `report.status == "passed_through"` and `report.warnings` contains `passthrough-no-image-content`.
  - `--re-ocr` on a native PDF → every page MRC'd (gate bypassed); `pages_skipped_verbatim == ()`.

### Regression — full matrix re-run

After merging, re-run the 31-PDF matrix from PR #11 and confirm:

- 30 of 31 inputs hit whole-doc passthrough at default (warning code `passthrough-no-image-content`).
- Wall time on those 30 inputs drops from 3-30s to <1s each.
- The 1 scan-derived input (Upadya Loynes) still compresses at 2.76× default / 3.03× fast — every page MRC'd.
- The 2 mixed inputs (Upadya Conlon, West) get partial MRC: photo pages compress, text pages verbatim. Net ratio at default may shift modestly.

## 6. Performance expectations

| Input class | Today (default) | After (default) |
|---|---:|---:|
| Pure-native PDF (30/31 of matrix) | 3-30s, runs full pipeline, output discarded | <1s, whole-doc shortcut |
| Pure-scan PDF (1/31) | 22s, MRC every page | 22s, MRC every page (no change) |
| Mixed (some photo pages) | full pipeline on every page | MRC photo pages only, verbatim copy text/vector pages |

## 7. Out of Scope

- Filling `tests/corpus/manifest.json` with real fixtures.
- Per-page OCR strategy decisions (text layer handling is already feature-complete in PR #11).
- A configurable image-area threshold (parse `Do` operators × XObject geometry). Defer until evidence shows image-byte-fraction is insufficient.
- A "`--force-mrc N1,N3`" page-list flag. YAGNI.

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Corrupt PDF makes per-page byte-counting throw | Default to `True` (MRC-worthy) on any per-page exception; emit a warning code; fail-safe to today's behavior. |
| Pathological PDF where image-byte-fraction misclassifies a page | The threshold is tunable per-call. Users can set `0.0` to disable the gate entirely or set lower/higher to tune. |
| Verbatim-copy bytes don't merge cleanly into the output | The verbatim path uses the EXACT 1-page slice the existing pre-page split already produces. Merge stage already merges these (it always has). |
| `pages_skipped_verbatim` field on CompressReport breaks downstream consumers | The field has a default `()` so existing code that constructs CompressReport without it continues to work. |
| `--re-ocr` interaction is unclear to users | Documented in CompressOptions docstring + CLI help: those flags force full MRC on every page (gate disabled for that run). |

## 9. Acceptance Criteria

The PR is ready to merge when ALL of the following are true:

1. `uv run pytest` passes (baseline + new tests).
2. `score_pages_for_mrc` returns `[True, False]` on a synthetic 2-page PDF with one image and one text page (test asserts).
3. A 100%-native PDF runs through `compress()` in <1s and returns `status="passed_through"` with `passthrough-no-image-content` in warnings (test asserts).
4. A 2-page mixed PDF returns `pages_skipped_verbatim=(1,)` and the verbatim page in the output is byte-identical to the input page (test asserts).
5. `--re-ocr` on a native PDF runs every page through MRC (test asserts).
6. The full 31-PDF matrix re-run shows 30/31 inputs hit whole-doc passthrough at <1s each.
7. `/dc` post-implementation review passes clean (no CRITICAL/MEDIUM findings).

## 10. Branch + PR Strategy

New branch `feat/per-page-selective-mrc` off `pre-public-sweep` (the still-open PR #11 base). Separate PR with its own focused review. Once both PRs merge to `main`, the public-flip readiness work and the per-page selectivity feature ship together.
