# Per-Page Selective MRC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip the MRC compression pipeline on pages that won't benefit, copying them verbatim from the input. Whole-doc passthrough on native PDFs falls out as the degenerate case where every page is verbatim. Implements the existing `PageStrategy.ALREADY_OPTIMIZED` enum value that was reserved but never wired up.

**Architecture:** A new `pdf_smasher/engine/page_classifier.py` exposes `score_pages_for_mrc(pdf_bytes) -> list[bool]` based on per-page image-byte-fraction. `compress()` runs it once, short-circuits to whole-doc passthrough when no page meets the threshold (and no flag forces full pipeline), and otherwise threads a per-page `mrc_worthy` hint into `_WorkerInput`. The worker's first action is to check `mrc_worthy`; when False it returns the unchanged 1-page slice as the `composed_bytes` of `_PageResult`. The merge stage is unchanged — it already accepts per-page bytes regardless of how they were produced.

**Tech Stack:** Python 3.14, `pikepdf`, `pytest`, `uv`. PEP 758 `except A, B:` valid.

**Pre-flight (run once before Task 1):**

```bash
git checkout feat/per-page-selective-mrc
uv run pytest tests/unit -q
```

Expect 278 passed (baseline). Branch is currently 1 commit ahead of `pre-public-sweep` (the spec doc).

---

## File Structure

**Create:**
- `pdf_smasher/engine/page_classifier.py` — `score_pages_for_mrc()` and a small private helper.
- `tests/unit/engine/test_page_classifier.py` — unit tests for the classifier.
- `tests/unit/test_compress_per_page_gate.py` — integration tests for the end-to-end behavior.

**Modify:**
- `pdf_smasher/types.py` — add `min_image_byte_fraction: float = 0.30` to `CompressOptions`; add `pages_skipped_verbatim: tuple[int, ...] = ()` to `CompressReport`.
- `pdf_smasher/cli/main.py` — add `--per-page-min-image-fraction` CLI flag and wire it into `_build_options`.
- `pdf_smasher/__init__.py` — add `mrc_worthy: bool = True` to `_WorkerInput`; in `compress()`, score pages once and either whole-doc passthrough or annotate each `_WorkerInput`; in the worker, fast-path return when `mrc_worthy=False`; populate `pages_skipped_verbatim` from results.
- `docs/PERFORMANCE.md` — replace stale numbers with re-run matrix data showing the new whole-doc shortcut behavior.
- `CHANGELOG.md` — add entry under `[Unreleased]`.

---

## Task 1: CompressOptions + CLI flag

**Files:**
- Modify: `pdf_smasher/types.py` (add field)
- Modify: `pdf_smasher/cli/main.py` (add flag, wire into `_build_options`)
- Test: `tests/unit/test_types.py` (assert default), `tests/unit/test_input_size_limits.py` (assert CLI default)

- [ ] **Step 1.1: Write failing test for CompressOptions default**

In `tests/unit/test_types.py`, add a new test:

```python
def test_default_min_image_byte_fraction_is_30_percent() -> None:
    opts = CompressOptions()
    assert opts.min_image_byte_fraction == 0.30
```

- [ ] **Step 1.2: Run test, see it fail**

```bash
uv run pytest tests/unit/test_types.py::test_default_min_image_byte_fraction_is_30_percent -v
```

Expected: `AttributeError: 'CompressOptions' object has no attribute 'min_image_byte_fraction'`.

- [ ] **Step 1.3: Add the field to CompressOptions**

In `pdf_smasher/types.py`, find the existing `# Limits` section (around line 78). Insert AFTER `max_input_mb`:

```python
    # Per-page MRC gate: pages whose image_xobject_bytes / page_byte_budget
    # is below this threshold are copied verbatim from the input — no
    # rasterize, no MRC pipeline, no Tesseract. Default 0.30 catches
    # native-export PDFs (PowerPoint/Word output) where the MRC pipeline
    # can't beat already-efficient encoding. Set to 0.0 to disable the
    # gate (force every page through MRC). The flags strip_text_layer
    # and re_ocr also disable the gate (every page MRCs).
    min_image_byte_fraction: float = 0.30
```

- [ ] **Step 1.4: Run test, see it pass**

```bash
uv run pytest tests/unit/test_types.py::test_default_min_image_byte_fraction_is_30_percent -v
```

Expected: PASSED.

- [ ] **Step 1.5: Add CLI flag**

In `pdf_smasher/cli/main.py`, find the existing `--re-ocr` flag block (just below `--strip-text-layer`). Add right after:

```python
    p.add_argument(
        "--per-page-min-image-fraction",
        type=float,
        default=0.30,
        help=(
            "Per-page MRC gate threshold. Pages with image_xobject_bytes / "
            "page_byte_budget below this fraction are copied verbatim instead "
            "of recompressed. Default 0.30 — catches native-export PDFs and "
            "skips them. Set to 0.0 to force the full MRC pipeline on every "
            "page."
        ),
    )
```

- [ ] **Step 1.6: Wire flag into `_build_options`**

In `pdf_smasher/cli/main.py`, find `_build_options` (around line 519). Add a line for the new flag, alongside the other CompressOptions kwargs:

```python
        min_image_byte_fraction=args.per_page_min_image_fraction,
```

- [ ] **Step 1.7: Add CLI default test**

In `tests/unit/test_input_size_limits.py`, add:

```python
def test_cli_default_per_page_min_image_fraction_is_30_percent() -> None:
    from pdf_smasher.cli.main import _parser

    parser = _parser()
    ns = parser.parse_args(["dummy.pdf", "-o", "out.pdf"])
    assert ns.per_page_min_image_fraction == 0.30
```

- [ ] **Step 1.8: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 1.9: Commit**

```bash
git add pdf_smasher/types.py pdf_smasher/cli/main.py tests/unit/test_types.py tests/unit/test_input_size_limits.py
git commit -m "feat(types): add min_image_byte_fraction option + CLI flag"
```

---

## Task 2: Page classifier

**Files:**
- Create: `pdf_smasher/engine/page_classifier.py`
- Create: `tests/unit/engine/test_page_classifier.py`

- [ ] **Step 2.1: Write failing tests**

Create `tests/unit/engine/test_page_classifier.py`:

```python
"""Tests for per-page MRC scoring."""

from __future__ import annotations

import io

import pikepdf

from pdf_smasher.engine.page_classifier import score_pages_for_mrc


def _make_text_only_pdf() -> bytes:
    """A pure-text PDF: one Helvetica-Tj page, no image XObjects."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
                Encoding=pikepdf.Name.WinAnsiEncoding,
            ),
        )
    )
    page.Contents = pdf.make_stream(
        b"BT /F1 24 Tf 100 700 Td (Hello World) Tj ET\n"
    )
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def _make_image_only_pdf(image_bytes: bytes = b"\x00" * 50_000) -> bytes:
    """A pure-image PDF: one page with a large /XObject /Image."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image_stream = pdf.make_stream(
        image_bytes,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=100,
        Height=100,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Filter=pikepdf.Name.FlateDecode,
    )
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=image_stream),
    )
    page.Contents = pdf.make_stream(
        b"q 612 0 0 792 0 0 cm /Im0 Do Q\n"
    )
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def test_text_only_page_returns_false() -> None:
    flags = score_pages_for_mrc(_make_text_only_pdf())
    assert flags == [False]


def test_image_dominated_page_returns_true() -> None:
    flags = score_pages_for_mrc(_make_image_only_pdf())
    assert flags == [True]


def test_mixed_pdf_returns_per_page_decisions() -> None:
    text_pdf = _make_text_only_pdf()
    image_pdf = _make_image_only_pdf()
    # Concatenate: append the image page into the text PDF.
    pdf = pikepdf.open(io.BytesIO(text_pdf))
    img_pdf = pikepdf.open(io.BytesIO(image_pdf))
    pdf.pages.append(img_pdf.pages[0])
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    img_pdf.close()
    pdf.close()
    flags = score_pages_for_mrc(buf.getvalue())
    assert flags == [False, True]


def test_threshold_override_disables_gate() -> None:
    # With threshold=0.0 every page is MRC-worthy.
    text_pdf = _make_text_only_pdf()
    flags = score_pages_for_mrc(text_pdf, min_image_byte_fraction=0.0)
    assert flags == [True]


def test_corrupt_input_falls_back_to_mrc_worthy() -> None:
    """Defensive default: if a page can't be analyzed, assume MRC-worthy
    (fail-safe to today's behavior)."""
    import pytest

    with pytest.raises(Exception):  # noqa: B017, BLE001 — pikepdf will raise on garbage
        # The classifier itself doesn't catch parse-time errors; that's
        # the caller's responsibility. This test documents that contract.
        score_pages_for_mrc(b"not a pdf at all")
```

- [ ] **Step 2.2: Run tests, see them fail**

```bash
uv run pytest tests/unit/engine/test_page_classifier.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pdf_smasher.engine.page_classifier'`.

- [ ] **Step 2.3: Create the classifier module**

Create `pdf_smasher/engine/page_classifier.py`:

```python
"""Per-page MRC-worthiness classifier.

Cheap signal: image_xobject_bytes / page_byte_budget per page. No
decoding, no rendering — just stream-length inspection. Returns one
bool per page (True = MRC-worthy, False = verbatim copy).

A page is MRC-worthy when its image-byte ratio meets or exceeds the
threshold. The default threshold (0.30) gives clean separation on
real-world inputs: native-export PDFs (PowerPoint/Word) sit at 0-15%;
scan-derived PDFs sit at 70-95%.

See ``docs/superpowers/specs/2026-04-27-per-page-selective-mrc-design.md``.
"""

from __future__ import annotations

import io

import pikepdf


def score_pages_for_mrc(
    pdf_bytes: bytes,
    *,
    password: str | None = None,
    min_image_byte_fraction: float = 0.30,
) -> list[bool]:
    """Return one bool per page: True = MRC-worthy, False = verbatim copy.

    Walks the input PDF once via pikepdf, computes per-page
    ``image_xobject_bytes / page_byte_budget``, returns True for pages
    whose ratio meets ``min_image_byte_fraction``. Pages where the
    analysis fails default to True (fail-safe — runs the existing MRC
    pipeline as a backstop).

    The denominator includes the page's content stream length plus the
    encoded byte size of every XObject (image AND form) referenced from
    that page's ``/Resources``. This is a fair "how much of this page is
    image data" measurement.
    """
    flags: list[bool] = []
    with pikepdf.open(io.BytesIO(pdf_bytes), password=password or "") as pdf:
        for page in pdf.pages:
            try:
                fraction = _page_image_byte_fraction(page)
            except Exception:  # noqa: BLE001 — defensive; any page-level error → MRC
                flags.append(True)
                continue
            flags.append(fraction >= min_image_byte_fraction)
    return flags


def _page_image_byte_fraction(page: pikepdf.Page) -> float:
    """Return image_xobject_bytes / page_byte_budget for one page.

    Numerator: sum of ``/Length`` for every ``/XObject /Image`` stream
    referenced from this page's ``/Resources/XObject`` dict.

    Denominator: ``len(content_stream) + sum(referenced_xobject_lengths)``,
    where referenced XObjects include both /Image and /Form (vector
    subforms). Floor at 1 byte to avoid division-by-zero on degenerate
    pages.
    """
    image_bytes = 0
    other_xobject_bytes = 0
    resources = page.obj.get("/Resources")
    if resources is not None:
        xobjects = resources.get("/XObject")
        if xobjects is not None:
            for xobj in xobjects.values():  # type: ignore[operator]
                if not isinstance(xobj, pikepdf.Stream):
                    continue
                length = int(xobj.get("/Length", 0) or 0)
                if xobj.get("/Subtype") == pikepdf.Name.Image:
                    image_bytes += length
                else:
                    other_xobject_bytes += length

    contents = page.obj.get("/Contents")
    content_bytes = 0
    if contents is not None:
        if isinstance(contents, pikepdf.Array):
            for s in contents:  # type: ignore[attr-defined]
                if isinstance(s, pikepdf.Stream):
                    content_bytes += int(s.get("/Length", 0) or 0)
        elif isinstance(contents, pikepdf.Stream):
            content_bytes = int(contents.get("/Length", 0) or 0)

    budget = max(1, content_bytes + image_bytes + other_xobject_bytes)
    return image_bytes / budget
```

- [ ] **Step 2.4: Run tests, see them pass**

```bash
uv run pytest tests/unit/engine/test_page_classifier.py -v
```

Expected: 5 passed.

- [ ] **Step 2.5: Run full suite**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 2.6: Commit**

```bash
git add pdf_smasher/engine/page_classifier.py tests/unit/engine/test_page_classifier.py
git commit -m "feat(engine): page-level MRC-worthiness classifier"
```

---

## Task 3: _WorkerInput.mrc_worthy + verbatim-copy fast path

**Files:**
- Modify: `pdf_smasher/__init__.py` (`_WorkerInput` field; worker fast path)

- [ ] **Step 3.1: Add the field to `_WorkerInput`**

In `pdf_smasher/__init__.py`, find the `class _WorkerInput:` definition (around line 147). Add a new field at the end:

```python
    input_page_pdf: bytes  # 1-page PDF extracted from source
    page_index: int  # 0-indexed position in original PDF
    page_size: tuple[float, float]  # (width_pt, height_pt)
    source_dpi: int
    bg_target_dpi: int
    effective_bg_codec: str
    options: CompressOptions
    is_safe: bool
    lev_ceiling: float
    ssim_floor: float
    mrc_worthy: bool = True  # False = verbatim-copy fast path; default True for back-compat
```

- [ ] **Step 3.2: Add the verbatim-copy fast path in the worker**

In `pdf_smasher/__init__.py`, find `def _process_single_page(winput: _WorkerInput)` (the per-page worker function). Find the line right after the imports + `_worker_t0 = time.monotonic()` setup and BEFORE the `# --- Rasterize input ---` comment.

Insert (using the same shape as the existing `skip_verify` synthetic verdict at lines ~599-609):

```python
    # ── Per-page MRC gate ────────────────────────────────────────────
    # If the page wasn't MRC-worthy (image-byte-fraction below the
    # threshold), skip the entire pipeline — return the unchanged
    # 1-page slice as the composed bytes, with a trivially-passing
    # verdict (same shape as skip_verify uses).
    if not winput.mrc_worthy:
        from pdf_smasher.engine.verifier import PageVerdict as _PageVerdict

        _verdict = _PageVerdict(
            page_index=-1,
            passed=True,
            lev=1.0,
            ssim_global=0.0,
            ssim_tile_min=0.0,
            digits_match=False,
            color_preserved=False,
        )
        return _PageResult(
            page_index=winput.page_index,
            composed_bytes=winput.input_page_pdf,
            strategy_name="already_optimized",
            verdict=_verdict,
            per_page_warnings=(),
            input_bytes=len(winput.input_page_pdf),
            output_bytes=len(winput.input_page_pdf),
            ratio=1.0,
            worker_wall_ms=int((time.monotonic() - _worker_t0) * 1000),
        )
```

- [ ] **Step 3.3: Run baseline tests (the new field has back-compat default; existing call sites still construct `_WorkerInput` without it)**

```bash
uv run pytest tests/unit -q
```

Expected: all green. (No tests touch the new branch yet.)

- [ ] **Step 3.4: Commit**

```bash
git add pdf_smasher/__init__.py
git commit -m "feat(engine): _WorkerInput.mrc_worthy + verbatim-copy worker fast path"
```

---

## Task 4: compress() — score pages, whole-doc shortcut, per-WorkerInput hint

**Files:**
- Modify: `pdf_smasher/__init__.py` (`compress()` body)

- [ ] **Step 4.1: Score pages and decide whole-doc shortcut**

In `pdf_smasher/__init__.py`, find the `compress()` body. Locate the section just AFTER the `_enforce_input_policy(tri, options, input_data)` call but BEFORE the per-page split. (This is roughly where `_selected_indices` is computed and the per-page split happens — search for `single_page_pdfs:`.)

Insert this block immediately after `_enforce_input_policy(tri, options, input_data)`:

```python
    # ── Per-page MRC gate ────────────────────────────────────────────
    # Score every page once: True = MRC-worthy, False = verbatim copy.
    # If no page meets the threshold AND no flag forces full MRC, the
    # input passes through unchanged at <1s wall time. Otherwise the
    # per-page flag is threaded into _WorkerInput and the worker
    # short-circuits the pipeline for verbatim pages.
    from pdf_smasher.engine.page_classifier import score_pages_for_mrc

    _force_full_pipeline = bool(options.re_ocr) or bool(options.strip_text_layer)
    if _force_full_pipeline:
        # --re-ocr / --strip-text-layer require every page to MRC.
        _mrc_flags = [True] * tri.pages
    else:
        _mrc_flags = score_pages_for_mrc(
            input_data,
            password=options.password,
            min_image_byte_fraction=options.min_image_byte_fraction,
        )

    if not any(_mrc_flags):
        # Whole-doc shortcut: every page is verbatim → return input unchanged.
        return _build_passthrough_report(
            input_data,
            pages=tri.pages,
            wall_ms=int((time.monotonic() - t0) * 1000),
            reason="no page meets the image-content threshold",
            warning_code="passthrough-no-image-content",
            correlation_id=correlation_id,
        )
```

The start-time variable in `compress()` is `t0` (defined at line 768 as `t0 = time.monotonic()`). The `_build_passthrough_report` helper takes `correlation_id` as a kwarg per `pdf_smasher/__init__.py:197-205`.

- [ ] **Step 4.2: Thread the per-page flag into `_WorkerInput`**

In `pdf_smasher/__init__.py`, find the `_WorkerInput(...)` construction (around line 984). Add `mrc_worthy=_mrc_flags[i]` to the kwargs:

```python
    winputs: list[_WorkerInput] = [
        _WorkerInput(
            input_page_pdf=single_page_pdfs[i],
            page_index=i,
            page_size=page_sizes[i],
            source_dpi=source_dpi,
            bg_target_dpi=bg_target_dpi,
            effective_bg_codec=effective_bg_codec,
            options=options,
            is_safe=is_safe,
            lev_ceiling=lev_ceiling,
            ssim_floor=ssim_floor,
            mrc_worthy=_mrc_flags[i],
        )
        for i in _selected_indices
    ]
```

- [ ] **Step 4.3: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green. (The whole-doc shortcut may activate on test PDFs; verify nothing regresses.)

- [ ] **Step 4.4: Commit**

```bash
git add pdf_smasher/__init__.py
git commit -m "feat(compress): per-page MRC gate + whole-doc passthrough shortcut"
```

---

## Task 5: CompressReport.pages_skipped_verbatim

**Files:**
- Modify: `pdf_smasher/types.py` (`CompressReport` field)
- Modify: `pdf_smasher/__init__.py` (populate the field at merge time)

- [ ] **Step 5.1: Add the field to CompressReport**

In `pdf_smasher/types.py`, find the `class CompressReport:` definition. Add a new field right before `warnings: tuple[str, ...] = ()`:

```python
    # 0-indexed page numbers that the per-page gate copied verbatim
    # from the input rather than running through the MRC pipeline.
    # Empty tuple = every page was MRC'd OR a whole-doc passthrough
    # fired (in which case status="passed_through").
    pages_skipped_verbatim: tuple[int, ...] = ()
```

- [ ] **Step 5.2: Track verbatim page indices in `_merge_result`**

In `pdf_smasher/__init__.py:946`, immediately after `page_pdfs_by_index: dict[int, bytes] = {}`, add:

```python
    _verbatim_pages: set[int] = set()
```

In `_merge_result` (defined at line 950), after the existing `strategy_counts[result.strategy_name] += 1` line (line 955), add:

```python
        if result.strategy_name == "already_optimized":
            _verbatim_pages.add(result.page_index)
```

The existing `strategy_counts` dict at line 941 already pre-allocates an `"already_optimized"` slot — the codebase was wired for this strategy name even though no producer existed before this PR.

- [ ] **Step 5.3: Populate the report field**

Find the `report = CompressReport(...)` call at line 1300. Add a kwarg right before `correlation_id=...` (so the field appears next to the other tuple fields like `warnings`):

```python
        pages_skipped_verbatim=tuple(sorted(_verbatim_pages)),
```

- [ ] **Step 5.4: Add an aggregate warning code when verbatim pages exist**

Find the `warnings_list: list[str] = []` block (line 922). After `_merge_result` has run (so after the `for result in ...` loop completes — search for "merge complete" emit or just before `report = CompressReport(`), add:

```python
    # If some-but-not-all pages were verbatim, emit an aggregate warning
    # so users see in the report that the gate fired on some pages.
    if _verbatim_pages and len(_verbatim_pages) < tri.pages:
        warnings_list.append(f"pages-skipped-verbatim:{len(_verbatim_pages)}")
```

- [ ] **Step 5.5: Run all tests**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 5.6: Commit**

```bash
git add pdf_smasher/types.py pdf_smasher/__init__.py
git commit -m "feat(report): populate pages_skipped_verbatim + warning code"
```

---

## Task 6: Integration tests

**Files:**
- Create: `tests/unit/test_compress_per_page_gate.py`

- [ ] **Step 6.1: Write the integration tests**

Create `tests/unit/test_compress_per_page_gate.py`:

```python
"""End-to-end behavior of the per-page MRC gate."""

from __future__ import annotations

import io

import pikepdf

from pdf_smasher import compress
from pdf_smasher.types import CompressOptions


def _make_text_only_pdf() -> bytes:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
                Encoding=pikepdf.Name.WinAnsiEncoding,
            ),
        )
    )
    page.Contents = pdf.make_stream(b"BT /F1 24 Tf 100 700 Td (Hello World) Tj ET\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def _make_image_only_pdf() -> bytes:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    image_stream = pdf.make_stream(
        b"\x00" * 50_000,
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=100,
        Height=100,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Filter=pikepdf.Name.FlateDecode,
    )
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=image_stream),
    )
    page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q\n")
    buf = io.BytesIO()
    pdf.save(buf, linearize=False)
    return buf.getvalue()


def test_native_pdf_hits_whole_doc_passthrough() -> None:
    """100% native PDF: whole-doc shortcut fires; output is byte-identical to input."""
    pdf_bytes = _make_text_only_pdf()
    out_bytes, report = compress(pdf_bytes, options=CompressOptions())
    assert out_bytes == pdf_bytes
    assert report.status == "passed_through"
    assert any("passthrough-no-image-content" in w for w in report.warnings)


def test_re_ocr_disables_the_gate() -> None:
    """--re-ocr forces every page through MRC even on a native PDF."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(re_ocr=True, skip_verify=True)
    # Should NOT passthrough; should run the full pipeline.
    # We don't assert on output bytes (the pipeline may inflate); we just
    # assert the status is NOT "passed_through" with the new warning.
    out_bytes, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


def test_strip_text_layer_disables_the_gate() -> None:
    """--strip-text-layer forces every page through MRC."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(strip_text_layer=True, skip_verify=True)
    out_bytes, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)


def test_threshold_zero_forces_full_pipeline() -> None:
    """min_image_byte_fraction=0.0 disables the gate (every page MRC-worthy)."""
    pdf_bytes = _make_text_only_pdf()
    options = CompressOptions(min_image_byte_fraction=0.0, skip_verify=True)
    out_bytes, report = compress(pdf_bytes, options=options)
    assert not any("passthrough-no-image-content" in w for w in report.warnings)
```

- [ ] **Step 6.2: Run tests**

```bash
uv run pytest tests/unit/test_compress_per_page_gate.py -v
```

Expected: all 4 passed.

- [ ] **Step 6.3: Run the full suite for regressions**

```bash
uv run pytest tests/unit -q
```

Expected: all green.

- [ ] **Step 6.4: Commit**

```bash
git add tests/unit/test_compress_per_page_gate.py
git commit -m "test(compress): integration tests for per-page MRC gate"
```

---

## Task 7: Docs + benchmark refresh

**Files:**
- Modify: `docs/PERFORMANCE.md` (replace stale numbers)
- Modify: `CHANGELOG.md` (`[Unreleased]` entry)

- [ ] **Step 7.1: Re-run the matrix**

```bash
uv run python /tmp/hankpdf-bench/run_matrix.py
column -t -s $'\t' /tmp/hankpdf-bench/matrix/results.tsv | head -5
```

Expected (after this PR): on native-export PDFs the `default` setting wall time drops from 3-30s to <1s. On the scan-derived input wall is unchanged. Capture a fresh `default` row's wall time for at least 3 inputs to update the docs.

- [ ] **Step 7.2: Patch docs/PERFORMANCE.md**

Find the section "Real-world matrix (31 PDFs × 8 settings = 248 runs)" added in PR #11. Append a subsection right after it:

```markdown
### Per-page MRC gate impact

After this feature lands, the per-page gate (`min_image_byte_fraction = 0.30` default) reroutes the 30 native-export PDFs through whole-doc passthrough at the top of `compress()` — no per-page workers, no merge stage. Wall time on those inputs drops from 3-30s to <1s each.

| Input class | Before (default) | After (default) | Mechanism |
|---|---:|---:|---|
| Pure-native PDF (30/31 of matrix) | 3-30s, full pipeline, output discarded by `--min-ratio` | <1s | whole-doc shortcut at top of `compress()` |
| Pure-scan PDF (1/31) | ~22s, MRC every page | ~22s | every page MRC'd (no change) |
| Mixed input (e.g., 50-page deck with 5 photo slides) | full pipeline on every page | photo pages MRC; text pages verbatim | per-page worker fast-path |

The gate disables itself on `--re-ocr` and `--strip-text-layer` runs (those flags require every page to be rasterized).
```

- [ ] **Step 7.3: Update CHANGELOG.md**

Append under `[Unreleased]`:

```markdown
- **Per-page selective MRC** — pages whose image-byte-fraction is below `CompressOptions.min_image_byte_fraction` (default 0.30) are now copied verbatim from the input instead of running through the MRC pipeline. On native-export PDFs every page is verbatim → whole-doc passthrough fires at the top of `compress()` → wall time drops from 3-30s to <1s. New CLI flag `--per-page-min-image-fraction`; new `CompressReport.pages_skipped_verbatim` field; new warning code `passthrough-no-image-content` (whole-doc) and `pages-skipped-verbatim:N` (partial). The `--re-ocr` and `--strip-text-layer` flags disable the gate (force every page through MRC). Closes the design gap left by `PageStrategy.ALREADY_OPTIMIZED` being defined but never wired up.
```

- [ ] **Step 7.4: Commit**

```bash
git add docs/PERFORMANCE.md CHANGELOG.md
git commit -m "docs(perf): document per-page MRC gate + refreshed numbers"
```

---

## Final verification

- [ ] **Run all tests + lint + format + mypy**

```bash
uv run pytest tests/unit -q
uv run ruff check pdf_smasher tests
uv run ruff format --check pdf_smasher tests
uv run mypy pdf_smasher
```

Expected: all green.

- [ ] **Acceptance criteria from the spec**

```bash
# 1. score_pages_for_mrc returns [True, False] on a 2-page mixed PDF
#    (covered by tests/unit/engine/test_page_classifier.py::test_mixed_pdf_returns_per_page_decisions)

# 2. Native PDF runs in <1s and returns status="passed_through"
#    (covered by tests/unit/test_compress_per_page_gate.py::test_native_pdf_hits_whole_doc_passthrough)

# 3. --re-ocr forces every page through MRC
#    (covered by tests/unit/test_compress_per_page_gate.py::test_re_ocr_disables_the_gate)
```

- [ ] **Optional matrix re-run for the docs commit**

```bash
uv run python /tmp/hankpdf-bench/run_matrix.py
```

Confirm 30/31 inputs hit `passthrough-no-image-content` warning at default.

- [ ] **Hand back to /jack-it-up Phase 5** (`/dc` review of the implementation).
