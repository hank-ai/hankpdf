# DCR Wave 1 Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 3 CRITICAL, 2 HIGH, 4 MEDIUM, 1 LOW-bundle findings from the DCR Wave 1 double-check review of the recent Phase-2b changes (skip_verify default flip, --max-output-mb chunking, --output-format JPEG/PNG/WebP image export, forkserver parallelism).

**Architecture:** Each task is a focused TDD cycle: RED test → GREEN fix → commit. Tasks are ordered to minimize rework — module relocations happen first (Task 1), then the security-relevant fixes (Tasks 2–6), then the UX/observability fixes (Tasks 7–11), then doc/cleanup (Tasks 12–15). No existing behavior is changed beyond the fixes listed; test count grows; all 216 current tests keep passing.

**Tech Stack:** Python 3.14 (PEP 758 makes unparenthesized `except` valid — don't re-parenthesize them). pytest, ruff `ALL`, mypy. pikepdf, pypdfium2, Pillow, Tesseract via subprocess. CLI via argparse. Progress via custom ProgressEvent + tqdm.

---

## File Structure

**Modified files:**
- `pdf_smasher/__init__.py` — add `_enforce_input_policy`; update `_process_single_page` skip_verify branch; remove `_WorkerInput.total_pages`; surface `set_forkserver_preload` failure as a warning; put `_ocr_pool` in a context manager.
- `pdf_smasher/cli/main.py` — image-export: add empty-pages guard, `_enforce_input_policy` call, progress callback, per-page error context, streaming writes, unified filename scheme; chunking: stale-file warning, unified filename scheme, warn when `--max-output-mb` is passed to image-export; new warnings for `--output-format`/extension mismatch; bound `--image-dpi` and `--pages` spec.
- `pdf_smasher/types.py` — no schema changes; update docstrings for `skip_verify` and `VerifierResult.status="skipped"` semantics.
- `pdf_smasher/engine/verifier.py` — `_VerifierAggregator` gains `mark_all_skipped()` to build a `VerifierResult(status="skipped", …)` cleanly.
- `docs/SPEC.md` — sync with current code: missing `CompressOptions` fields, new warning codes, new CLI flags, flipped defaults, new `ProgressEvent` type, `strategy_distribution` in CompressReport.
- `README.md` — add image-export + chunking example block.

**Relocated files (Task 1):**
- `pdf_smasher/chunking.py` → `pdf_smasher/engine/chunking.py`
- `pdf_smasher/image_export.py` → `pdf_smasher/engine/image_export.py`
- Test paths unchanged (already at `tests/unit/engine/test_chunking.py` / `test_image_export.py`).

**New test files:**
- `tests/integration/test_cli_image_export.py` — CLI-level image-export behavior (currently zero coverage at CLI level).
- `tests/integration/test_cli_chunking.py` — CLI-level `--max-output-mb` behavior (currently zero coverage).
- `tests/unit/test_cli_pages_spec.py` — pure unit tests for `_parse_pages_spec`.

---

## Task 1: Relocate chunking + image_export into `pdf_smasher/engine/`

**Rationale:** Every other engine helper lives in `pdf_smasher/engine/` (`background.py`, `compose.py`, `foreground.py`, `mask.py`, `ocr.py`, `rasterize.py`, `strategy.py`, `text_layer.py`, `triage.py`, `verifier.py`). The new modules broke the convention. The tests already live at `tests/unit/engine/`, so the test paths are already consistent with the future home — we just need to move the source.

**Files:**
- Move: `pdf_smasher/chunking.py` → `pdf_smasher/engine/chunking.py`
- Move: `pdf_smasher/image_export.py` → `pdf_smasher/engine/image_export.py`
- Modify: `pdf_smasher/cli/main.py` (two import sites)
- Modify: `tests/unit/engine/test_chunking.py` (one import)
- Modify: `tests/unit/engine/test_image_export.py` (one import)

- [ ] **Step 1: Confirm current test baseline**

Run: `uv run pytest -q`
Expected: `216 passed`. If not 216 green, stop and investigate first.

- [ ] **Step 2: Move chunking.py into engine/**

```bash
git mv pdf_smasher/chunking.py pdf_smasher/engine/chunking.py
```

- [ ] **Step 3: Move image_export.py into engine/**

```bash
git mv pdf_smasher/image_export.py pdf_smasher/engine/image_export.py
```

- [ ] **Step 4: Update `pdf_smasher/cli/main.py` imports**

Find and replace exactly two lines:

```python
# BEFORE (inside _run_image_export):
    from pdf_smasher.image_export import render_pages_as_images
# AFTER:
    from pdf_smasher.engine.image_export import render_pages_as_images
```

```python
# BEFORE (inside main(), chunking path):
            from pdf_smasher.chunking import split_pdf_by_size
# AFTER:
            from pdf_smasher.engine.chunking import split_pdf_by_size
```

Hoist both to the top of the file while we're here:

```python
# At top of pdf_smasher/cli/main.py, with the other `from pdf_smasher` imports:
from pdf_smasher.engine.chunking import split_pdf_by_size
from pdf_smasher.engine.image_export import render_pages_as_images
```

Remove the original function-local imports.

- [ ] **Step 5: Update test imports**

In `tests/unit/engine/test_chunking.py`, change:

```python
from pdf_smasher.chunking import split_pdf_by_size
```

to:

```python
from pdf_smasher.engine.chunking import split_pdf_by_size
```

In `tests/unit/engine/test_image_export.py`, change:

```python
from pdf_smasher.image_export import render_pages_as_images
```

to:

```python
from pdf_smasher.engine.image_export import render_pages_as_images
```

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q`
Expected: `216 passed`. Ruff: `uv run ruff check pdf_smasher tests scripts` → `All checks passed!`. Mypy: `uv run mypy pdf_smasher` → `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: move chunking + image_export into pdf_smasher/engine/

Restores convention: every other engine helper (compose, rasterize,
mask, verifier, etc.) lives under pdf_smasher/engine/. Move the two
new Phase-2b modules into the same directory. Hoist the CLI's
function-local imports to the top of the file (no laziness win — the
imports touch only pikepdf + pdfium, which are already loaded)."
```

---

## Task 2: Fix `skip_verify` to emit `VerifierResult(status="skipped", …)`

**Rationale (CRITICAL):** When `skip_verify=True` (the default since the Phase-2b flip), `_process_single_page` synthesizes a perfect `PageVerdict` (passed=True, lev=0.0, ssim=1.0). These roll up to `VerifierResult(status="pass", …)` — indistinguishable from a real clean run. But `VerifierResult.status` already defines `"skipped"` as a valid literal; we just never use it. Downstream callers that gate on `report.verifier.status == "pass"` (legal/archival workflows) would ship unverified output believing it was verified. The README still promises the content-preservation invariant; the code no longer honors it by default.

**Files:**
- Modify: `pdf_smasher/engine/verifier.py` — `_VerifierAggregator` gains a method to build a skipped result.
- Modify: `pdf_smasher/__init__.py` — new aggregator method is called once when `skip_verify` is set; each worker returns a marker instead of a synthesized pass; the marker also appends a `verifier-skipped` entry to `warnings_list`.
- Modify: `pdf_smasher/engine/verifier.py` — `_VerifierAggregator` also needs `failure_summary()` to handle "skipped" → empty message.
- Test: `tests/unit/engine/test_verifier.py` (extend) and `tests/integration/test_compress_api.py` (extend).

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/engine/test_verifier.py`:

```python
def test_verifier_aggregator_skipped_result() -> None:
    """When skip_verify is set, aggregator.skipped_result() returns a
    VerifierResult with status='skipped' and NaN-ish metrics rather than
    a fake 'pass' with perfect metrics."""
    from pdf_smasher.engine.verifier import _VerifierAggregator

    agg = _VerifierAggregator()
    result = agg.skipped_result()
    assert result.status == "skipped"
    # No pages were ever merged, so there are no failing pages to report.
    assert result.failing_pages == ()
    # Metrics should NOT claim perfect fidelity — callers reading the
    # dataclass must not mistake "skipped" for "verified clean".
    # We use sentinel values (0.0 for ratios that would be 1.0 on pass,
    # 1.0 for ratios that would be 0.0 on pass) so any gating code keying
    # on e.g. `ssim_global >= 0.92` WILL fail-closed.
    assert result.ssim_global == 0.0
    assert result.ssim_min_tile == 0.0
    assert result.ocr_levenshtein == 1.0  # max possible = "total drift"
    assert result.digit_multiset_match is False
    assert result.color_preserved is False
```

Append to `tests/integration/test_compress_api.py`:

```python
def test_compress_skip_verify_reports_status_skipped() -> None:
    """With skip_verify=True (the default), the returned CompressReport
    must surface status='skipped' rather than a fake 'pass', and append
    a 'verifier-skipped' warning."""
    pdf_in = _make_fake_scan(["HELLO"])
    _, report = compress(pdf_in, options=CompressOptions(skip_verify=True))
    assert report.verifier.status == "skipped", (
        f"expected skipped, got {report.verifier.status}"
    )
    assert any(w == "verifier-skipped" for w in report.warnings), (
        f"expected 'verifier-skipped' in warnings; got {report.warnings}"
    )


def test_compress_verify_true_reports_real_status() -> None:
    """With skip_verify=False, the verifier runs and status is 'pass' or
    'fail' (not 'skipped'). No 'verifier-skipped' warning."""
    pdf_in = _make_fake_scan(["HELLO"])
    _, report = compress(pdf_in, options=CompressOptions(skip_verify=False))
    assert report.verifier.status in ("pass", "fail"), (
        f"expected pass|fail, got {report.verifier.status}"
    )
    assert not any(w == "verifier-skipped" for w in report.warnings)
```

- [ ] **Step 2: Run tests — confirm they fail**

Run:

```bash
uv run pytest tests/unit/engine/test_verifier.py::test_verifier_aggregator_skipped_result tests/integration/test_compress_api.py::test_compress_skip_verify_reports_status_skipped tests/integration/test_compress_api.py::test_compress_verify_true_reports_real_status -v
```

Expected: all three FAIL.
- `test_verifier_aggregator_skipped_result` → `AttributeError: '_VerifierAggregator' object has no attribute 'skipped_result'`.
- `test_compress_skip_verify_reports_status_skipped` → `AssertionError: expected skipped, got pass`.
- `test_compress_verify_true_reports_real_status` → PASS (the pre-existing verifier path still returns pass/fail).

- [ ] **Step 3: Add `skipped_result()` on `_VerifierAggregator`**

In `pdf_smasher/engine/verifier.py`, inside `class _VerifierAggregator`, add a method (right after `result()`):

```python
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
            structural_match=True,
            color_preserved=False,
            failing_pages=(),
        )
```

- [ ] **Step 4: Route `compress()` through `skipped_result()` when `skip_verify`**

In `pdf_smasher/__init__.py`, find the block that currently synthesizes the fake-pass `PageVerdict` inside `_process_single_page` (the `if options.skip_verify:` branch that builds `PageVerdict(..., passed=True, lev=0.0, ssim_global=1.0, ...)`), and replace it with a sentinel verdict that the aggregator can recognize OR just let the worker return a verdict and have the aggregator's post-loop `result()` call be swapped for `skipped_result()` in the parent when skip_verify is set.

Easier, more explicit approach (parent-side swap):

Find in `compress()` the line(s) like:

```python
verifier_result = verifier_agg.result()
```

Replace with:

```python
verifier_result = (
    verifier_agg.skipped_result() if options.skip_verify else verifier_agg.result()
)
```

Then remove the synthesized `PageVerdict` in `_process_single_page` entirely — when `skip_verify=True`, the worker still builds a trivial PageVerdict for the aggregator (since `merge` expects one), but the aggregator output is replaced at the parent. To avoid emitting misleading per-page metrics, the worker's synthesized verdict should have the SAME fail-closed sentinels. Keep its `passed=True` (so the aggregator does not append a failing-pages entry) but make the rest:

```python
    if options.skip_verify:
        from pdf_smasher.engine.verifier import PageVerdict as _PageVerdict

        verdict = _PageVerdict(
            page_index=-1,
            passed=True,           # don't add to failing_pages
            lev=1.0,               # sentinel: max drift
            ssim_global=0.0,       # sentinel: no similarity
            ssim_tile_min=0.0,
            digits_match=False,
            color_preserved=False,
        )
```

Also append `"verifier-skipped"` to `warnings_list` ONCE per run (not per page). Find the block where skip_verify is first observed (top of compress(), right after `options = options or CompressOptions()`), or more naturally right before the per-page dispatch:

```python
    if options.skip_verify:
        warnings_list.append("verifier-skipped")
```

Exact line: put it right after `warnings_list = []` initialization in `compress()`.

- [ ] **Step 5: Run tests — confirm they pass**

Run: same as step 2.

Expected: all three PASS.

- [ ] **Step 6: Run full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 218 passed (2 new), ruff clean, mypy clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m 'fix(verifier): emit status="skipped" when skip_verify=True

CRITICAL (DCR Wave 1). When skip_verify was the default, compress()
returned CompressReport(verifier=VerifierResult(status="pass",
ssim_global=1.0, ...)) — indistinguishable from a real clean run.
Downstream callers keying on verifier.status=="pass" (legal/archival
gatekeepers) would ship unverified output believing it was verified.

Fix: add _VerifierAggregator.skipped_result() that emits status="skipped"
with fail-closed sentinel metrics (ssim=0, lev=1) so any gating code
fails-closed. Parent compress() swaps to skipped_result() when
skip_verify is set; workers still synthesize a trivially-passing
per-page verdict (so failing_pages stays empty) but with the same
fail-closed metrics. Appends a one-shot "verifier-skipped" entry to
CompressReport.warnings so batch pipelines can grep for it.'
```

---

## Task 3: Guard against empty `--pages` in image-export mode

**Rationale (CRITICAL):** `_parse_pages_spec(" ")` and `_parse_pages_spec(",,,")` both return `set()`. The main PDF path has a guard (`if only_pages is not None and not only_pages: raise CompressError`). The image-export path does not — `_run_image_export` with an empty `only_pages` set passes validation, calls `render_pages_as_images(pdf_bytes, page_indices=[], ...)` which returns `[]`, then silently writes zero files and exits 0. A user running `hankpdf in.pdf -o out.jpg --pages "$SEL"` where `$SEL` is an unset env-var-expansion gets no output and no error.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — add guard at top of `_run_image_export`.
- Test: `tests/integration/test_cli_image_export.py` (new file).

- [ ] **Step 1: Create the test file**

Create `tests/integration/test_cli_image_export.py`:

```python
"""CLI-level integration tests for --output-format jpeg|png|webp."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher.cli.main import main


def _make_pdf(tmp_path, n_pages: int = 2):  # type: ignore[no-untyped-def]
    """Build an N-page white PDF at tmp_path/in.pdf."""
    pdf = pikepdf.new()
    for _ in range(n_pages):
        img = Image.new("RGB", (850, 1100), color="white")
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 40)
        except OSError:
            font = ImageFont.load_default(size=40)
        draw.text((100, 500), "X", fill="black", font=font)
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        xobj = pdf.make_stream(
            buf.getvalue(),
            Type=pikepdf.Name.XObject,
            Subtype=pikepdf.Name.Image,
            Width=img.width,
            Height=img.height,
            ColorSpace=pikepdf.Name.DeviceRGB,
            BitsPerComponent=8,
            Filter=pikepdf.Name.DCTDecode,
        )
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=xobj))
        page.Contents = pdf.make_stream(b"q 612 0 0 792 0 0 cm /Scan Do Q\n")
    path = tmp_path / "in.pdf"
    pdf.save(path)
    return path


@pytest.mark.integration
def test_image_export_empty_pages_spec_exits_usage_error(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """Empty --pages string must return EXIT_USAGE (40), not silently exit 0.
    Regression gate: DCR Wave 1 flagged that env-var-expansion producing an
    empty string used to silently succeed with zero files written."""
    in_path = _make_pdf(tmp_path, n_pages=2)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path), "--pages", ""])
    assert rc == 40, f"expected EXIT_USAGE=40, got {rc}"
    # No file should be written.
    assert not out_path.exists(), f"unexpected file written: {out_path}"
    err = capsys.readouterr().err
    assert "--pages" in err and ("empty" in err.lower() or "no pages" in err.lower()), (
        f"expected clear stderr message about empty pages; got: {err!r}"
    )
```

- [ ] **Step 2: Run test — confirm it fails**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_empty_pages_spec_exits_usage_error -v`

Expected: FAIL with `AssertionError: expected EXIT_USAGE=40, got 0` (or similar — the CLI currently exits 0 silently).

- [ ] **Step 3: Add the guard**

In `pdf_smasher/cli/main.py`, find `_run_image_export(...)`. Right after the `if only_pages is not None: ... page_indices = ...` block and before calling `render_pages_as_images`, add:

```python
    if not page_indices:
        print(
            "error: --pages parsed to an empty set (no pages selected); "
            "provide at least one 1-indexed page number",
            file=sys.stderr,
        )
        return EXIT_USAGE
```

Exact placement: inside `_run_image_export`, after:

```python
    if only_pages is not None:
        # ... out-of-range check + page_indices = sorted(p - 1 for p in only_pages)
    else:
        page_indices = list(range(tri.pages))
```

Add the guard immediately after this block.

- [ ] **Step 4: Run test — confirm pass**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_empty_pages_spec_exits_usage_error -v`

Expected: PASS.

- [ ] **Step 5: Full suite + lint**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 219 passed, clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m 'fix(cli): image-export rejects empty --pages spec with EXIT_USAGE

CRITICAL (DCR Wave 1). An empty --pages string (e.g. from unset
env-var expansion) silently returned zero files and exited 0. The
main PDF path guards against this; the image-export path did not.

Now returns exit 40 (EXIT_USAGE) with a clear stderr message.'
```

---

## Task 4: Extract `_enforce_input_policy` and apply to image-export

**Rationale (CRITICAL security):** `compress()` enforces the encrypted/signed/certified/oversize/max_input_mb gates that SECURITY.md §2 and §3 depend on. `_run_image_export` calls only `triage()` (for the page count) and proceeds without these enforcement checks. A user running `hankpdf encrypted-patient-records.pdf -o pages.jpg --output-format jpeg` will export unencrypted JPEGs to disk if pdfium can render the pages (common: owner-password-only files). This silently bypasses the tool's stated safety model.

**Files:**
- Modify: `pdf_smasher/__init__.py` — extract `_enforce_input_policy(tri, options, input_data)` and call it from `compress()` at the existing enforcement site.
- Modify: `pdf_smasher/cli/main.py` — call the new helper in `_run_image_export` right after triage.
- Test: `tests/integration/test_cli_image_export.py` (append).

- [ ] **Step 1: Write failing tests**

Append to `tests/integration/test_cli_image_export.py`:

```python
@pytest.mark.integration
def test_image_export_refuses_encrypted_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Image-export must enforce the same gates as compress(): an encrypted
    PDF without a password must be refused with EXIT_ENCRYPTED (10).
    Regression gate: DCR Wave 1 found image-export bypassed this check."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    in_path = tmp_path / "enc.pdf"
    pdf.save(in_path, encryption=pikepdf.Encryption(user="secret", owner="o"))
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 10, f"expected EXIT_ENCRYPTED=10, got {rc}"
    assert not out_path.exists()


@pytest.mark.integration
def test_image_export_refuses_signed_pdf(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Signed PDFs must be refused in image-export too (EXIT_SIGNED=11)."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.Root["/AcroForm"] = pikepdf.Dictionary(
        SigFlags=3, Fields=pikepdf.Array([]),
    )
    in_path = tmp_path / "signed.pdf"
    pdf.save(in_path)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path)])
    assert rc == 11, f"expected EXIT_SIGNED=11, got {rc}"
    assert not out_path.exists()
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_refuses_encrypted_pdf tests/integration/test_cli_image_export.py::test_image_export_refuses_signed_pdf -v`

Expected: both FAIL (image-export currently bypasses these gates).

- [ ] **Step 3: Extract `_enforce_input_policy` in `pdf_smasher/__init__.py`**

Add a new private function at module scope (near the top, after the dataclass definitions):

```python
def _enforce_input_policy(
    tri: TriageReport,
    options: CompressOptions,
    input_data: bytes,
) -> None:
    """Apply every safety gate that compress() enforces on the input.

    Raises the appropriate exception from the CompressError hierarchy if
    a gate is tripped. Both compress() and the CLI's image-export path
    must route through this so users get the same refusal behavior
    regardless of the chosen output format.
    """
    if tri.classification == "require-password" and options.password is None:
        msg = "input is encrypted; supply CompressOptions.password"
        raise EncryptedPDFError(msg)

    if tri.is_certified_signature and not options.allow_certified_invalidation:
        msg = (
            "input carries a certifying signature; "
            "--allow-certified-invalidation required"
        )
        raise CertifiedSignatureError(msg)

    if tri.is_signed and not options.allow_signed_invalidation:
        msg = "input is signed; --allow-signed-invalidation required"
        raise SignedPDFError(msg)

    if options.max_pages is not None and tri.pages > options.max_pages:
        msg = f"input has {tri.pages} pages; max_pages={options.max_pages}"
        raise OversizeError(msg)

    input_mb = len(input_data) / (1024 * 1024)
    if input_mb > options.max_input_mb:
        msg = f"input {input_mb:.1f} MB exceeds max_input_mb={options.max_input_mb}"
        raise OversizeError(msg)
```

Then in `compress()`, find the block that currently does these checks inline (search for `if tri.classification == "require-password"`) and replace the whole block (the four consecutive `raise` sites) with a single call:

```python
    _enforce_input_policy(tri, options, input_data)
```

- [ ] **Step 4: Call the helper from `_run_image_export`**

In `pdf_smasher/cli/main.py`, `_run_image_export`, find:

```python
    try:
        tri = triage(input_bytes)
    except CompressError as e:
        print(f"refused: {e}", file=sys.stderr)
        return EXIT_CORRUPT
```

Immediately after this, before the `if only_pages is not None:` block, add:

```python
    # Enforce the same safety gates as compress(). Encrypted/signed/oversize
    # PDFs must be refused regardless of output format.
    from pdf_smasher import _enforce_input_policy

    try:
        _enforce_input_policy(tri, _build_options(args), input_bytes)
    except EncryptedPDFError as e:
        print(f"refused: encrypted without password ({e})", file=sys.stderr)
        return EXIT_ENCRYPTED
    except CertifiedSignatureError as e:
        print(f"refused: certifying signature ({e})", file=sys.stderr)
        return EXIT_CERTIFIED_SIG
    except SignedPDFError as e:
        print(f"refused: signed PDF ({e})", file=sys.stderr)
        return EXIT_SIGNED
    except OversizeError as e:
        print(f"refused: oversize ({e})", file=sys.stderr)
        return EXIT_OVERSIZE
```

Note: `_build_options(args)` is already called elsewhere in `main()`; in the image-export path we previously called it before dispatching. If that ordering was different, thread the already-built `options` through `_run_image_export` as an arg instead of rebuilding — check the existing signature and keep the invocation consistent.

- [ ] **Step 5: Run — confirm pass**

Run: `uv run pytest tests/integration/test_cli_image_export.py -v`
Expected: all tests pass (new gate tests plus previous empty-pages test).

- [ ] **Step 6: Full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 221 passed, clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "fix(cli): image-export enforces encrypted/signed/oversize gates

CRITICAL security (DCR Wave 1). _run_image_export called triage() for
page count but skipped the enforcement checks compress() runs. An
encrypted (owner-password-only) or signed PDF whose pdfium can render
was exported silently. Extract _enforce_input_policy() in __init__.py
and call from both compress() and _run_image_export — single source
of truth for the refusal contract."
```

---

## Task 5: Bound `--image-dpi` to prevent memory-exhaustion DoS

**Rationale (HIGH security):** `--image-dpi 99999` on a 14400-pt PDF page (UserUnit tricks push this higher) computes `target_w = 14400 * 99999 / 72 ≈ 20 million` pixels per side — a 1.2 PB RGB buffer request. Even at default DPI 150, a conforming 14400×14400 pt page is 30000×30000 = 2.7 GB. The MRC path has the triage decompression-bomb check, but image-export skips it.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — validate `--image-dpi` at argparse via a custom type function.
- Modify: `pdf_smasher/engine/image_export.py` — belt-and-suspenders: check raster pixel budget before each page.
- Test: `tests/integration/test_cli_image_export.py` (append).

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_cli_image_export.py`:

```python
@pytest.mark.integration
def test_image_export_rejects_excessive_dpi(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """--image-dpi 5000 on a standard letter page would produce a
    42000x54000 RGB buffer (~6 GB). Refuse before allocating."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main([str(in_path), "-o", str(out_path), "--image-dpi", "5000"])
    assert rc == 40, f"expected EXIT_USAGE=40, got {rc}"
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_rejects_excessive_dpi -v`
Expected: FAIL (currently exits 0 after allocating a huge buffer or crashing with MemoryError).

- [ ] **Step 3: Add DPI validator in `pdf_smasher/cli/main.py`**

Add near the other helpers (above `_parser()`):

```python
_MAX_IMAGE_DPI = 1200  # 300 archival + 4x headroom; above this = OOM risk

def _positive_dpi(raw: str) -> int:
    """argparse type for --image-dpi. Reject unreasonably large or
    non-positive values that would trigger a memory-exhaustion DoS in
    rasterize_page()."""
    try:
        n = int(raw)
    except ValueError as e:
        msg = f"invalid int: {raw!r}"
        raise argparse.ArgumentTypeError(msg) from e
    if n < 1:
        msg = f"--image-dpi must be >= 1 (got {n})"
        raise argparse.ArgumentTypeError(msg)
    if n > _MAX_IMAGE_DPI:
        msg = (
            f"--image-dpi capped at {_MAX_IMAGE_DPI} (got {n}); higher "
            "values can exceed addressable memory on realistic page sizes"
        )
        raise argparse.ArgumentTypeError(msg)
    return n
```

Then change the `--image-dpi` argument declaration:

```python
# BEFORE:
    p.add_argument(
        "--image-dpi",
        type=int,
        default=150,
        ...
    )
# AFTER:
    p.add_argument(
        "--image-dpi",
        type=_positive_dpi,
        default=150,
        ...
    )
```

- [ ] **Step 4: Belt-and-suspenders in image_export**

In `pdf_smasher/engine/image_export.py`, inside `render_pages_as_images`, after the `raster = rasterize_page(...)` call and before the encode step, add:

```python
        # Defense-in-depth: even with the CLI's --image-dpi cap, a PDF
        # with extreme MediaBox dimensions (UserUnit multipliers etc.)
        # could still produce a huge raster. Refuse > ~2 GB RGB buffers.
        _max_px = 2 * 1024 * 1024 * 1024 // 3  # 2 GiB / 3 bytes per pixel
        if raster.width * raster.height > _max_px:
            msg = (
                f"page {page_index + 1} rasterized to "
                f"{raster.width}x{raster.height} px — exceeds the "
                f"decompression-bomb cap ({_max_px / (1024 * 1024):.0f} MB "
                "of raw pixels). Lower --image-dpi or --pages to proceed."
            )
            raise ValueError(msg)
```

- [ ] **Step 5: Run — confirm pass**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_rejects_excessive_dpi -v`
Expected: PASS.

- [ ] **Step 6: Full suite**

Run: `uv run pytest -q`
Expected: 222 passed.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m 'fix(cli): bound --image-dpi at 1200 to prevent memory DoS

HIGH security (DCR Wave 1). --image-dpi 99999 on a crafted PDF (or
even --image-dpi 5000 on standard letter) allocates multi-GB RGB
buffers. argparse now rejects values > 1200. Belt-and-suspenders:
render_pages_as_images also refuses any single-page raster larger
than ~700 Mpx (~2 GB raw RGB).'
```

---

## Task 6: Bound `--pages` spec size

**Rationale (HIGH):** `_parse_pages_spec("1-99999999999")` materializes a Python `set` of 100 billion ints — gigabytes of memory, long before any page check. A user typo or attacker-supplied env var crashes the process.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — `_parse_pages_spec` caps individual range sizes.
- Test: `tests/unit/test_cli_pages_spec.py` (new file).

- [ ] **Step 1: Create unit test file**

Create `tests/unit/test_cli_pages_spec.py`:

```python
"""Pure unit tests for the CLI --pages spec parser."""

from __future__ import annotations

import pytest

from pdf_smasher.cli.main import _parse_pages_spec


def test_single_page() -> None:
    assert _parse_pages_spec("5") == {5}


def test_range() -> None:
    assert _parse_pages_spec("3-5") == {3, 4, 5}


def test_comma_list() -> None:
    assert _parse_pages_spec("1,3,5") == {1, 3, 5}


def test_mixed_comma_range() -> None:
    assert _parse_pages_spec("1,3-5,10") == {1, 3, 4, 5, 10}


def test_single_range_is_same_as_page() -> None:
    assert _parse_pages_spec("7-7") == {7}


def test_backward_range_rejected() -> None:
    with pytest.raises(ValueError, match="range"):
        _parse_pages_spec("5-3")


def test_zero_rejected() -> None:
    with pytest.raises(ValueError, match="1-indexed"):
        _parse_pages_spec("0")


def test_empty_returns_empty_set() -> None:
    # The CLI layer treats this as an error (exits 40) but the parser
    # itself returns an empty set — the guard is at call-sites.
    assert _parse_pages_spec("") == set()
    assert _parse_pages_spec(",,,") == set()


def test_extremely_large_range_rejected() -> None:
    """Regression gate: DCR Wave 1 flagged --pages '1-99999999999' as
    a memory-exhaustion DoS. Cap range sizes to 1,000,000."""
    with pytest.raises(ValueError, match="too large|cap"):
        _parse_pages_spec("1-99999999999")


def test_range_at_cap_accepted() -> None:
    # 1_000_000 pages is the limit; cap+1 is rejected.
    result = _parse_pages_spec("1-1000000")
    assert len(result) == 1_000_000


def test_range_over_cap_rejected() -> None:
    with pytest.raises(ValueError, match="too large|cap"):
        _parse_pages_spec("1-1000001")
```

- [ ] **Step 2: Run — confirm fails**

Run: `uv run pytest tests/unit/test_cli_pages_spec.py -v`
Expected: most pass, but `test_extremely_large_range_rejected`, `test_range_over_cap_rejected` FAIL. `test_range_at_cap_accepted` may even hang / OOM.

If test_range_at_cap_accepted hangs, kill it with Ctrl-C and proceed — implementing the cap will unblock it.

- [ ] **Step 3: Add cap to `_parse_pages_spec`**

In `pdf_smasher/cli/main.py`, find `_parse_pages_spec` and add the cap check inside the range-parsing branch:

```python
_MAX_PAGES_RANGE = 1_000_000


def _parse_pages_spec(spec: str) -> set[int]:
    out: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            try:
                lo = int(lo_s)
                hi = int(hi_s)
            except ValueError as e:
                msg = f"invalid range {part!r}: must be integers"
                raise ValueError(msg) from e
            if lo < 1 or hi < lo:
                msg = f"invalid range {part!r}: must be 1-indexed, lo <= hi"
                raise ValueError(msg)
            if hi - lo + 1 > _MAX_PAGES_RANGE:
                msg = (
                    f"range {part!r} too large: cap is {_MAX_PAGES_RANGE:,} "
                    "pages per range to prevent memory exhaustion"
                )
                raise ValueError(msg)
            out.update(range(lo, hi + 1))
        else:
            try:
                n = int(part)
            except ValueError as e:
                msg = f"invalid page {part!r}: must be an integer"
                raise ValueError(msg) from e
            if n < 1:
                msg = f"invalid page {part!r}: must be 1-indexed (got {n})"
                raise ValueError(msg)
            out.add(n)
    return out
```

- [ ] **Step 4: Run tests — confirm pass**

Run: `uv run pytest tests/unit/test_cli_pages_spec.py -v`
Expected: all 11 tests pass.

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 233 passed (11 new), clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m 'fix(cli): cap --pages range size at 1,000,000 to prevent DoS

HIGH security (DCR Wave 1). --pages "1-99999999999" materialized a
100-billion-element int set — process OOMed. Cap individual range
sizes at 1,000,000 and raise ValueError above. New unit test suite
for _parse_pages_spec covers the range cap, valid/invalid inputs,
and the empty-set contract.'
```

---

## Task 7: Add progress + per-page error context to image-export

**Rationale (HIGH observability):** Rendering 200 pages at 300 DPI PNG takes many minutes and emits zero progress. A mid-render failure has no page index and no stage info in the exception. The MRC path has tqdm + `_page_error_context`; image-export has nothing equivalent.

**Files:**
- Modify: `pdf_smasher/engine/image_export.py` — add optional `progress_callback` param; wrap each iteration with per-page error context.
- Modify: `pdf_smasher/cli/main.py` — `_run_image_export` creates a tqdm bar and feeds events.
- Test: `tests/unit/engine/test_image_export.py` (append).

- [ ] **Step 1: Write failing test**

Append to `tests/unit/engine/test_image_export.py`:

```python
def test_render_pages_emits_progress_events() -> None:
    """render_pages_as_images accepts an optional progress_callback and
    fires one event per completed page."""
    pdf_bytes = _make_pdf(3)
    events: list[tuple[int, int]] = []

    def _cb(phase: str, current: int, total: int) -> None:
        if phase == "page_done":
            events.append((current, total))

    render_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1, 2],
        image_format="jpeg",
        dpi=72,
        progress_callback=_cb,
    )
    # 3 page_done events, 1-indexed current, total=3.
    assert events == [(1, 3), (2, 3), (3, 3)], f"got {events}"


def test_render_pages_per_page_error_context() -> None:
    """When rasterize_page fails on page N, the raised exception must
    contain 'page {N+1}' so logs tell the user which page."""
    pdf_bytes = _make_pdf(5)
    with pytest.raises(Exception, match="page 3"):
        render_pages_as_images(
            pdf_bytes,
            page_indices=[2],  # 0-indexed -> displayed as 1-indexed page 3
            image_format="jpeg",
            dpi=72,
            _force_rasterize_error_for_test=True,  # new test hook
        )
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/unit/engine/test_image_export.py::test_render_pages_emits_progress_events tests/unit/engine/test_image_export.py::test_render_pages_per_page_error_context -v`
Expected: both FAIL (`progress_callback` param doesn't exist, no error context).

- [ ] **Step 3: Update `render_pages_as_images`**

Replace the whole signature and loop in `pdf_smasher/engine/image_export.py`:

```python
def render_pages_as_images(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int = 150,
    jpeg_quality: int = 75,
    jpeg_subsampling: int = _JPEG_SUBSAMPLING_444,
    png_compress_level: int = 6,
    webp_quality: int = 80,
    webp_lossless: bool = False,
    webp_method: int = _WEBP_METHOD_DEFAULT,
    progress_callback: "Callable[[str, int, int], None] | None" = None,
    _force_rasterize_error_for_test: bool = False,
) -> list[bytes]:
    """... (keep existing docstring; add:)

    ``progress_callback`` receives ``(phase, current, total)`` triples:
    - ``('page_start', i, total)`` at the top of each iteration.
    - ``('page_done',  i, total)`` after each page is encoded.

    Exceptions raised during rasterize or encode are wrapped to include
    ``page {i + 1}/{total}`` in the message.
    """
    if image_format not in _SUPPORTED_FORMATS:
        msg = f"image_format must be one of {_SUPPORTED_FORMATS}; got {image_format!r}"
        raise ValueError(msg)
    if not page_indices:
        return []

    total = len(page_indices)
    out: list[bytes] = []
    for pos, page_index in enumerate(page_indices, start=1):
        if progress_callback is not None:
            progress_callback("page_start", pos, total)
        try:
            if _force_rasterize_error_for_test:
                msg = "forced test error"
                raise RuntimeError(msg)
            raster = rasterize_page(pdf_bytes, page_index=page_index, dpi=dpi)
            # Belt-and-suspenders pixel-budget check — from Task 5.
            _max_px = 2 * 1024 * 1024 * 1024 // 3
            if raster.width * raster.height > _max_px:
                msg = (
                    f"rasterized to {raster.width}x{raster.height} px — "
                    "exceeds decompression-bomb cap"
                )
                raise ValueError(msg)
            buf = io.BytesIO()
            rgb = raster.convert("RGB")
            if image_format == "jpeg":
                rgb.save(
                    buf,
                    format="JPEG",
                    quality=jpeg_quality,
                    subsampling=jpeg_subsampling,
                )
            elif image_format == "png":
                rgb.save(
                    buf,
                    format="PNG",
                    compress_level=png_compress_level,
                    optimize=(png_compress_level == _PNG_OPTIMIZE_LEVEL),
                )
            else:  # webp
                rgb.save(
                    buf,
                    format="WEBP",
                    quality=webp_quality,
                    lossless=webp_lossless,
                    method=webp_method,
                )
            out.append(buf.getvalue())
        except Exception as exc:
            msg = (
                f"image export failed on page {page_index + 1}/{total}: {exc}"
            )
            raise RuntimeError(msg) from exc
        if progress_callback is not None:
            progress_callback("page_done", pos, total)
    return out
```

Add the import at the top of the file:

```python
from collections.abc import Callable
```

- [ ] **Step 4: Wire tqdm in `_run_image_export`**

In `pdf_smasher/cli/main.py`, inside `_run_image_export`, after building `page_indices` and before calling `render_pages_as_images`, set up a tqdm bar:

```python
    from tqdm import tqdm  # type: ignore[import-untyped]

    _bar: "tqdm | None" = None
    if not args.quiet:
        _bar = tqdm(
            total=len(page_indices),
            desc=f"{image_format}",
            unit="pg",
            file=sys.stderr,
            dynamic_ncols=True,
            leave=True,
        )

    def _progress(phase: str, current: int, total: int) -> None:
        if _bar is not None and phase == "page_done":
            _bar.update(1)

    try:
        images = render_pages_as_images(
            input_bytes,
            page_indices=page_indices,
            image_format=image_format,  # type: ignore[arg-type]
            dpi=args.image_dpi,
            jpeg_quality=args.jpeg_quality,
            png_compress_level=args.png_compress_level,
            webp_quality=args.webp_quality,
            webp_lossless=args.webp_lossless,
            progress_callback=_progress,
        )
    finally:
        if _bar is not None:
            _bar.close()
```

- [ ] **Step 5: Run tests — confirm pass**

Run: `uv run pytest tests/unit/engine/test_image_export.py -v`
Expected: all image-export unit tests pass (existing ones unaffected, new ones green).

- [ ] **Step 6: Full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 235 passed, clean.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m 'feat(image-export): progress callback + per-page error context

HIGH observability (DCR Wave 1). Rendering 200 pages at 300 DPI used
to print zero progress and surface cryptic "Image could not be saved"
errors. Now renders with a tqdm bar (pg/s, ETA, count). Each failure
includes "page N/total" in the message so logs tell on-call exactly
which page failed during which stage.'
```

---

## Task 8: Stream image-export writes instead of buffering all pages

**Rationale (HIGH OOM):** `_run_image_export` calls `images = render_pages_as_images(...)` and ONLY then iterates to write. For 300 DPI × 400 PNG pages that's ~8 GB resident before the first write. A mid-render failure discards all prior work. Better: render + encode + write each page as a streaming generator.

**Files:**
- Modify: `pdf_smasher/engine/image_export.py` — add `iter_pages_as_images` generator counterpart (keep `render_pages_as_images` for backward compatibility but have it wrap the generator).
- Modify: `pdf_smasher/cli/main.py` — `_run_image_export` uses the generator and writes as it yields.
- Test: `tests/unit/engine/test_image_export.py` (append).

- [ ] **Step 1: Write failing test**

Append to `tests/unit/engine/test_image_export.py`:

```python
def test_iter_pages_yields_bytes_lazily() -> None:
    """iter_pages_as_images must be a generator, yielding one encoded
    image per iteration without materializing the whole list."""
    import types

    pdf_bytes = _make_pdf(3)
    it = iter_pages_as_images(
        pdf_bytes,
        page_indices=[0, 1, 2],
        image_format="jpeg",
        dpi=72,
    )
    assert isinstance(it, types.GeneratorType)
    first = next(it)
    assert first[:2] == b"\xff\xd8"
    rest = list(it)
    assert len(rest) == 2
```

Add the import at the top of the same test file:

```python
from pdf_smasher.engine.image_export import iter_pages_as_images, render_pages_as_images
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/unit/engine/test_image_export.py::test_iter_pages_yields_bytes_lazily -v`
Expected: FAIL (no `iter_pages_as_images` export).

- [ ] **Step 3: Refactor `render_pages_as_images` around a generator**

In `pdf_smasher/engine/image_export.py`, extract the loop body into `iter_pages_as_images` and have `render_pages_as_images` be a thin `list(iter_pages_as_images(...))` wrapper:

```python
def iter_pages_as_images(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int = 150,
    jpeg_quality: int = 75,
    jpeg_subsampling: int = _JPEG_SUBSAMPLING_444,
    png_compress_level: int = 6,
    webp_quality: int = 80,
    webp_lossless: bool = False,
    webp_method: int = _WEBP_METHOD_DEFAULT,
    progress_callback: Callable[[str, int, int], None] | None = None,
    _force_rasterize_error_for_test: bool = False,
) -> "Iterator[bytes]":
    """Streaming counterpart to render_pages_as_images. Yields one
    encoded image per requested page, never buffers more than one in
    memory. Callers can write-as-they-go to avoid OOM on huge batches."""
    if image_format not in _SUPPORTED_FORMATS:
        msg = f"image_format must be one of {_SUPPORTED_FORMATS}; got {image_format!r}"
        raise ValueError(msg)

    total = len(page_indices)
    for pos, page_index in enumerate(page_indices, start=1):
        if progress_callback is not None:
            progress_callback("page_start", pos, total)
        try:
            # ... (exact same body as Task 7's loop, but `yield buf.getvalue()`
            # instead of `out.append(buf.getvalue())`)
        except Exception as exc:
            msg = f"image export failed on page {page_index + 1}/{total}: {exc}"
            raise RuntimeError(msg) from exc
        if progress_callback is not None:
            progress_callback("page_done", pos, total)


def render_pages_as_images(
    pdf_bytes: bytes,
    page_indices: list[int],
    *,
    image_format: ImageFormat,
    dpi: int = 150,
    jpeg_quality: int = 75,
    jpeg_subsampling: int = _JPEG_SUBSAMPLING_444,
    png_compress_level: int = 6,
    webp_quality: int = 80,
    webp_lossless: bool = False,
    webp_method: int = _WEBP_METHOD_DEFAULT,
    progress_callback: Callable[[str, int, int], None] | None = None,
    _force_rasterize_error_for_test: bool = False,
) -> list[bytes]:
    """Eager counterpart to iter_pages_as_images. Kept for backward
    compatibility; prefer iter_pages_as_images for large batches."""
    return list(iter_pages_as_images(
        pdf_bytes,
        page_indices,
        image_format=image_format,
        dpi=dpi,
        jpeg_quality=jpeg_quality,
        jpeg_subsampling=jpeg_subsampling,
        png_compress_level=png_compress_level,
        webp_quality=webp_quality,
        webp_lossless=webp_lossless,
        webp_method=webp_method,
        progress_callback=progress_callback,
        _force_rasterize_error_for_test=_force_rasterize_error_for_test,
    ))
```

Update the `from collections.abc import Callable` import to also include `Iterator`:

```python
from collections.abc import Callable, Iterator
```

- [ ] **Step 4: Update `_run_image_export` to stream**

Replace the "compute then write" pattern in `pdf_smasher/cli/main.py` `_run_image_export`:

```python
    # (previous flow: images = render_pages_as_images(...); then loop writes)
    # New flow: iterate + write each as it yields.

    from pdf_smasher.engine.image_export import iter_pages_as_images

    args.output.parent.mkdir(parents=True, exist_ok=True)
    out_ext = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[image_format]
    valid_image_exts = {"jpg", "jpeg", "png", "webp"}
    base = args.output.stem
    parent = args.output.parent
    requested_ext = args.output.suffix.lower()
    final_ext = requested_ext if requested_ext.lstrip(".") in valid_image_exts else out_ext

    # Single-page vs multi-page naming is known up front from page_indices.
    n = len(page_indices)
    if n == 1:
        # Stdout short-circuit
        if str(args.output) == "-":
            for blob in iter_pages_as_images(
                input_bytes,
                page_indices=page_indices,
                image_format=image_format,  # type: ignore[arg-type]
                dpi=args.image_dpi,
                jpeg_quality=args.jpeg_quality,
                png_compress_level=args.png_compress_level,
                webp_quality=args.webp_quality,
                webp_lossless=args.webp_lossless,
                progress_callback=_progress,
            ):
                sys.stdout.buffer.write(blob)
            return EXIT_OK
        # Single file
        target = args.output if requested_ext.lstrip(".") in valid_image_exts \
                              else parent / f"{base}{final_ext}"
        for blob in iter_pages_as_images(
            input_bytes,
            page_indices=page_indices,
            image_format=image_format,  # type: ignore[arg-type]
            dpi=args.image_dpi,
            jpeg_quality=args.jpeg_quality,
            png_compress_level=args.png_compress_level,
            webp_quality=args.webp_quality,
            webp_lossless=args.webp_lossless,
            progress_callback=_progress,
        ):
            target.write_bytes(blob)
        if not args.quiet:
            print(
                f"wrote {target} ({target.stat().st_size:,} bytes, "
                f"{image_format} @ {args.image_dpi} DPI)",
                file=sys.stderr,
            )
    else:
        # Stdout rejection
        if str(args.output) == "-":
            print(
                "error: stdout (-o -) supports exactly one image but "
                f"{n} pages were selected. Add --pages N (e.g., --pages 1) "
                "to pick a single page.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        # Multi-page: stream each page to its own file as it yields.
        total_bytes = 0
        for page_idx, blob in zip(
            page_indices,
            iter_pages_as_images(
                input_bytes,
                page_indices=page_indices,
                image_format=image_format,  # type: ignore[arg-type]
                dpi=args.image_dpi,
                jpeg_quality=args.jpeg_quality,
                png_compress_level=args.png_compress_level,
                webp_quality=args.webp_quality,
                webp_lossless=args.webp_lossless,
                progress_callback=_progress,
            ),
            strict=True,
        ):
            target = parent / f"{base}_{page_idx + 1:03d}{final_ext}"
            target.write_bytes(blob)
            total_bytes += len(blob)
            if not args.quiet:
                print(
                    f"wrote {target.name} ({len(blob):,} bytes, page {page_idx + 1})",
                    file=sys.stderr,
                )
        if not args.quiet:
            print(
                f"[hankpdf] exported {n} {image_format} pages "
                f"({total_bytes:,} total bytes, {args.image_dpi} DPI)",
                file=sys.stderr,
            )
    return EXIT_OK
```

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: 236 passed (1 new), clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m 'perf(image-export): stream writes via iter_pages_as_images generator

HIGH OOM (DCR Wave 1). 300 DPI * 400-page PNG export used to buffer
all ~8 GB in memory before writing anything. If page 137 failed,
pages 1-136 were discarded. Now streams: render + encode + write
each page as the generator yields. Memory is O(1) in page count.

render_pages_as_images retained for backward compatibility as a
thin list(iter_pages_as_images(...)) wrapper.'
```

---

## Task 9: Unified zero-padded 1-indexed filename scheme + stale-chunk warning

**Rationale (MEDIUM):** PDF chunks currently use `{base}_{idx}{ext}` (0-indexed, no padding) — `out_0.pdf … out_10.pdf … out_9.pdf` sorts wrong lexically. Image-export already uses `{base}_{idx + 1:03d}{ext}` — zero-padded, 1-indexed, sorts right. Standardize on the image-export form everywhere. Also: pre-existing `{base}_{n}{ext}` files from a previous run are silently overwritten; we should warn the user.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — the chunking write block (in `main()`, post-compress).
- Test: `tests/integration/test_cli_chunking.py` (new file).

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_cli_chunking.py`:

```python
"""CLI-level integration tests for --max-output-mb."""

from __future__ import annotations

import io

import pikepdf
import pytest
from PIL import Image

from pdf_smasher.cli.main import main


def _make_big_pdf(tmp_path, n_pages: int = 4, payload_kb_per_page: int = 250):  # type: ignore[no-untyped-def]
    """Produce an N-page PDF whose serialized size blows past any small
    --max-output-mb cap, so chunking engages."""
    import secrets

    pdf = pikepdf.new()
    for _ in range(n_pages):
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        filler = secrets.token_bytes(payload_kb_per_page * 1024)
        stream = pdf.make_stream(filler)
        page["/Filler"] = stream
    path = tmp_path / "big.pdf"
    pdf.save(path)
    return path


@pytest.mark.integration
def test_chunked_output_uses_zero_padded_1_indexed_names(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With --max-output-mb triggering chunking, output files must be
    named {base}_NNN{ext} (zero-padded, 1-indexed). DCR Wave 1 flagged
    the 0-indexed no-pad form as sort-breaking past 9 chunks."""
    in_path = _make_big_pdf(tmp_path, n_pages=4, payload_kb_per_page=300)
    out_path = tmp_path / "smol.pdf"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.5",
        "--accept-drift",
        "--no-ocr",
    ])
    assert rc == 0, f"expected EXIT_OK=0, got {rc}"
    chunks = sorted(tmp_path.glob("smol_*.pdf"))
    assert len(chunks) >= 2, f"expected chunking; got {chunks}"
    for p in chunks:
        # {base}_001.pdf etc. -- stem ends with "_" followed by 3 digits
        stem = p.stem
        suffix_chunk = stem.rsplit("_", 1)[-1]
        assert suffix_chunk.isdigit(), f"expected numeric suffix, got {stem}"
        assert len(suffix_chunk) == 3, f"expected 3-digit zero-pad, got {stem}"
        # 1-indexed: there should be a _001
    first_chunks = [p for p in chunks if p.stem.endswith("_001")]
    assert len(first_chunks) == 1, (
        f"expected one _001 chunk; got chunks={chunks}"
    )


@pytest.mark.integration
def test_chunked_output_warns_on_stale_siblings(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """If a chunked write would leave stale _999 files from a prior run,
    warn the user. DCR Wave 1: silent stomp on pre-existing chunks is a
    correctness risk."""
    in_path = _make_big_pdf(tmp_path, n_pages=3, payload_kb_per_page=300)
    (tmp_path / "smol_099.pdf").write_bytes(b"stale data")
    out_path = tmp_path / "smol.pdf"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "0.5",
        "--accept-drift",
        "--no-ocr",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "stale" in err.lower(), (
        f"expected 'stale' in stderr warning; got: {err!r}"
    )
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/integration/test_cli_chunking.py -v`
Expected: both FAIL — first on `suffix_chunk.isdigit()` or the `_001` naming; second on missing stale warning.

- [ ] **Step 3: Update the chunking write block**

In `pdf_smasher/cli/main.py`, find the `if args.max_output_mb is None: ... else: ... for idx, chunk in enumerate(chunks): ...` block and replace the multi-chunk branch with zero-padded 1-indexed naming + stale-file detection:

```python
        else:
            max_bytes = int(args.max_output_mb * 1024 * 1024)
            chunks = split_pdf_by_size(output_bytes, max_bytes=max_bytes)
            if len(chunks) == 1:
                args.output.write_bytes(chunks[0])
            else:
                base = args.output.stem
                ext = args.output.suffix
                parent = args.output.parent

                # Detect stale chunks from prior runs that will not be
                # overwritten because our new chunk count is smaller.
                # {base}_NNN{ext} with NNN > len(chunks) are stragglers.
                import re as _re

                _chunk_re = _re.compile(
                    rf"^{_re.escape(base)}_(\d{{3}}){_re.escape(ext)}$",
                )
                stale: list[Path] = []
                if parent.exists():
                    for existing in parent.iterdir():
                        m = _chunk_re.match(existing.name)
                        if m is not None and int(m.group(1)) > len(chunks):
                            stale.append(existing)

                written_paths: list[Path] = []
                for idx, chunk in enumerate(chunks, start=1):
                    p = parent / f"{base}_{idx:03d}{ext}"
                    p.write_bytes(chunk)
                    written_paths.append(p)

                oversize = [p for p in written_paths if p.stat().st_size > max_bytes]
                if not args.quiet:
                    print(
                        f"[hankpdf] wrote {len(chunks)} chunks "
                        f"({args.max_output_mb:.1f} MB cap); "
                        f"sizes: {[f'{p.stat().st_size / (1024 * 1024):.2f} MB' for p in written_paths]}",
                        file=sys.stderr,
                    )
                    if oversize:
                        print(
                            f"[hankpdf] warning: {len(oversize)} chunk(s) "
                            "exceed the cap because they contain a single "
                            f"oversize page: {[p.name for p in oversize]}",
                            file=sys.stderr,
                        )
                    if stale:
                        print(
                            f"[hankpdf] warning: {len(stale)} stale chunk "
                            f"file(s) from a previous run remain in {parent}: "
                            f"{[p.name for p in stale]}. Remove them manually "
                            "if they no longer belong to this output.",
                            file=sys.stderr,
                        )
```

- [ ] **Step 4: Run — confirm pass**

Run: `uv run pytest tests/integration/test_cli_chunking.py -v`
Expected: both pass.

- [ ] **Step 5: Full suite**

Run: `uv run pytest -q`
Expected: 238 passed (2 new), clean.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m 'fix(cli): zero-padded 1-indexed chunk names + stale-chunk warning

MEDIUM (DCR Wave 1). PDF chunks used {base}_{idx}{ext} starting at 0
with no padding — lex-sort broke past 9 chunks. Align with the
image-export scheme: {base}_{idx:03d}{ext} with idx starting at 1.
Also detect stale {base}_NNN{ext} files from prior runs that survive
a new run with fewer chunks, and print a warning listing them.'
```

---

## Task 10: Warn when `--max-output-mb` is passed to image-export mode

**Rationale (MEDIUM):** `--max-output-mb` is a PDF-only concept. When the user passes it together with `--output-format jpeg` (or an image extension on `-o`), we currently do nothing — no warning, no honor. The stdout path warns; image-export should too.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — emit a stderr warning at the top of `_run_image_export` when `--max-output-mb` is set.
- Test: `tests/integration/test_cli_image_export.py` (append).

- [ ] **Step 1: Failing test**

Append to `tests/integration/test_cli_image_export.py`:

```python
@pytest.mark.integration
def test_image_export_warns_on_max_output_mb(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    in_path = _make_pdf(tmp_path, n_pages=1)
    out_path = tmp_path / "out.jpg"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--max-output-mb", "5",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "--max-output-mb" in err and "image" in err.lower(), (
        f"expected warning about --max-output-mb in image mode; got: {err!r}"
    )
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_image_export_warns_on_max_output_mb -v`
Expected: FAIL.

- [ ] **Step 3: Add the warning**

In `pdf_smasher/cli/main.py`, at the top of `_run_image_export` (right after the docstring), add:

```python
    if args.max_output_mb is not None and not args.quiet:
        print(
            "[hankpdf] warning: --max-output-mb applies only to PDF output; "
            "ignored in image-export mode",
            file=sys.stderr,
        )
```

- [ ] **Step 4: Run — confirm pass**

Run: same as step 2.
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`

```bash
git add -A
git commit -m 'fix(cli): warn when --max-output-mb is ignored in image-export

MEDIUM (DCR Wave 1). --max-output-mb is a PDF-only concept. When
passed with --output-format jpeg/png/webp it was silently ignored.
Now emits a stderr warning consistent with the stdout-path behavior.'
```

---

## Task 11: Warn on `--output-format` / extension mismatch

**Rationale (LOW-MEDIUM UX):** `hankpdf in.pdf -o out.pdf --output-format jpeg` silently writes a JPEG to a file named `out.jpg`. Users who ran the command expected `out.pdf`. Detect the mismatch early and warn.

**Files:**
- Modify: `pdf_smasher/cli/main.py` — after `resolved_format` is computed in `main()`, warn on mismatch.

- [ ] **Step 1: Failing test**

Append to `tests/integration/test_cli_image_export.py`:

```python
@pytest.mark.integration
def test_output_format_extension_mismatch_warns(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    """-o out.pdf --output-format jpeg should warn the user that we're
    writing a JPEG regardless of the .pdf extension."""
    in_path = _make_pdf(tmp_path, n_pages=1)
    # Intentional mismatch
    out_path = tmp_path / "out.pdf"
    rc = main([
        str(in_path),
        "-o", str(out_path),
        "--output-format", "jpeg",
    ])
    assert rc == 0
    err = capsys.readouterr().err
    assert "extension" in err.lower() or "overrides" in err.lower(), (
        f"expected mismatch warning in stderr; got: {err!r}"
    )
```

- [ ] **Step 2: Run — confirm fail**

Run: `uv run pytest tests/integration/test_cli_image_export.py::test_output_format_extension_mismatch_warns -v`
Expected: FAIL.

- [ ] **Step 3: Add the warning in `main()`**

In `pdf_smasher/cli/main.py`, find the block:

```python
    if args.output_format is not None:
        resolved_format = args.output_format
    else:
        ext = args.output.suffix.lower().lstrip(".") if args.output else ""
        resolved_format = {...}.get(ext, "pdf")
```

Right after it, add:

```python
    # Warn if an explicit --output-format overrides the -o extension.
    if args.output_format is not None and args.output is not None:
        ext = args.output.suffix.lower().lstrip(".")
        implicit = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(
            ext, "pdf",
        )
        if implicit != "pdf" and implicit != resolved_format and not args.quiet:
            print(
                f"[hankpdf] warning: --output-format {resolved_format} "
                f"overrides the .{ext} extension; output will be written "
                f"as {resolved_format} regardless of the filename suffix",
                file=sys.stderr,
            )
```

- [ ] **Step 4: Run — confirm pass**

Run: same as step 2.

- [ ] **Step 5: Full suite + commit**

```bash
git add -A
git commit -m 'fix(cli): warn when --output-format overrides -o extension

LOW-MEDIUM UX (DCR Wave 1). -o out.pdf --output-format jpeg silently
wrote out.jpg. Users expected out.pdf; no explanation for the rename.
Now emits a stderr warning making the override visible.'
```

---

## Task 12: Drop `_WorkerInput.total_pages` dead field

**Rationale (LOW cleanup):** The field is set, captured into a local (`total = winput.total_pages`), then `del total`'d at the end of `_process_single_page` with a comment explaining it's "proof that the local isn't accidentally unused." It's not used for anything. Drop it.

**Files:**
- Modify: `pdf_smasher/__init__.py` — remove the field from `_WorkerInput`, the local unpack, and the `del total`.

- [ ] **Step 1: Run current tests** (baseline)

Run: `uv run pytest -q`
Expected: 242 passed (roughly — depends on prior tasks landing).

- [ ] **Step 2: Remove the field and its uses**

In `pdf_smasher/__init__.py`:

1. Remove `total_pages: int` from `_WorkerInput` (the field declaration).
2. In `_process_single_page`, remove `total = winput.total_pages`.
3. Remove `del total` (the dead-variable marker).
4. Remove `total_pages=tri.pages,` from every `_WorkerInput(...)` call-site.

Search and remove these lines.

- [ ] **Step 3: Run tests — confirm still green**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: same count, still green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m 'refactor: drop _WorkerInput.total_pages dead field

LOW cleanup (DCR Wave 1). Field was assigned, captured into a local
named "total", then `del total` to prove it is unused. That is the
definition of dead weight. Remove the field, the capture, and the
del. Fewer bytes over the multiprocessing marshaling boundary.'
```

---

## Task 13: `_ocr_pool` context-manager lifetime

**Rationale (LOW robustness):** `_ocr_pool = ThreadPoolExecutor(max_workers=2)` is constructed near the top of `_process_single_page`; `_ocr_pool.shutdown(wait=False)` is called only on the happy path near the bottom. Any exception between construction and shutdown leaks the pool (plus whatever Tesseract subprocess threads are in flight). Wrap in a `with` block.

**Files:**
- Modify: `pdf_smasher/__init__.py` — `_process_single_page`.

- [ ] **Step 1: Write a regression test**

Append to `tests/integration/test_compress_api.py`:

```python
def test_ocr_pool_cleaned_up_on_worker_exception() -> None:
    """If a worker raises mid-page, the OCR ThreadPoolExecutor must be
    closed (no lingering tesseract-waiting threads)."""
    import gc
    import threading

    pdf_in = _make_fake_scan(["HELLO"])

    # Baseline thread count
    before = threading.active_count()

    # Force an engine error by passing a bad option: a negative
    # target_bg_dpi triggers a rasterize / compose error deep in the
    # pipeline. Catch the raised CompressError.
    from pdf_smasher import CompressError

    with pytest.raises(CompressError):
        compress(
            pdf_in,
            options=CompressOptions(
                skip_verify=False,
                target_bg_dpi=-1,  # invalid — causes an engine error
            ),
        )

    gc.collect()
    after = threading.active_count()
    # Allow some slack for tqdm/forkserver background threads, but
    # don't tolerate 10+ leaked OCR threads.
    assert after - before <= 5, (
        f"excessive thread leak: before={before}, after={after}"
    )
```

- [ ] **Step 2: Run — likely flaky/skip**

Run: `uv run pytest tests/integration/test_compress_api.py::test_ocr_pool_cleaned_up_on_worker_exception -v`

If the test is too flaky to be reliable (thread counts are OS-dependent), mark it `@pytest.mark.skipif(sys.platform != "darwin", reason="thread-count test reliable on macOS only")` or drop this test and rely on the structural review — the `with` refactor is a correctness improvement regardless.

- [ ] **Step 3: Wrap in context manager**

In `pdf_smasher/__init__.py`, `_process_single_page`, find the current structure:

```python
    _ocr_pool = ThreadPoolExecutor(max_workers=2)
    # ... 100 lines of work ...
    _ocr_pool.shutdown(wait=False)
```

Refactor into:

```python
    with ThreadPoolExecutor(max_workers=2) as _ocr_pool:
        # ... 100 lines of work ...
        # (no explicit shutdown — `with` handles it)
        pass
```

The `with`'s `__exit__` calls `shutdown(wait=True)` by default, ensuring all futures are drained on both success and exception paths.

- [ ] **Step 4: Run — confirm pass**

Run: full suite.
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m 'refactor: OCR thread pool lifetime via context manager

LOW robustness (DCR Wave 1). Any exception between pool construction
and explicit shutdown used to leak the ThreadPoolExecutor plus its
in-flight Tesseract subprocess threads. Wrap in `with` so __exit__
always drains — regardless of happy path vs exception.'
```

---

## Task 14: Surface `set_forkserver_preload` failure as a warning

**Rationale (LOW observability):** `contextlib.suppress(ValueError, RuntimeError)` around `ctx.set_forkserver_preload(...)` silently eats the error. If preload fails, every worker re-imports the 2-3 s import chain — a massive silent regression.

**Files:**
- Modify: `pdf_smasher/__init__.py` — replace `suppress` with try/except that appends a warning.

- [ ] **Step 1: Replace suppress with try/except**

In `pdf_smasher/__init__.py`, find:

```python
            if chosen_method == "forkserver":
                import contextlib as _contextlib

                with _contextlib.suppress(ValueError, RuntimeError):
                    ctx.set_forkserver_preload([...])
```

Replace with:

```python
            if chosen_method == "forkserver":
                try:
                    ctx.set_forkserver_preload([...])  # keep same module list
                except (ValueError, RuntimeError) as _e:
                    warnings_list.append(
                        f"forkserver-preload-failed-{type(_e).__name__}",
                    )
```

- [ ] **Step 2: Full suite**

Run: `uv run pytest -q && uv run ruff check pdf_smasher tests scripts && uv run mypy pdf_smasher`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m 'observability: surface set_forkserver_preload failure as warning

LOW observability (DCR Wave 1). `contextlib.suppress` silently
swallowed preload failures; each subsequent worker then paid the
2-3s import cost. Now appends "forkserver-preload-failed-<exc-type>"
to CompressReport.warnings so users can grep for it.'
```

---

## Task 15: Update `docs/SPEC.md` to reflect Phase-2b + DCR fixes

**Rationale (MEDIUM doc drift):** SPEC.md §1.1 is missing `max_workers`, `skip_verify`, `accept_drift`, `progress_callback`, `only_pages`, `strategy_distribution`, `ProgressEvent`. §1.1 still documents `ocr: bool = True` when the code has been `False` since commit 30b81d1. §2.1 lists removed flags and omits the new ones (`--max-output-mb`, `--output-format`, `--image-dpi`, `--jpeg-quality`, `--png-compress-level`, `--webp-quality`, `--webp-lossless`, `--skip-verify`, `--verify`, `--pages`, `--accept-drift`, `--max-workers`). §8.5 missing `verifier-skipped`, `forkserver-preload-failed-*`, stale-chunk, image-export error codes.

**Files:**
- Modify: `docs/SPEC.md` — §1.1 CompressOptions, §1.1 CompressReport, §1.2 compress() signature, §2.1 CLI flags, §7.2 error codes, §8.5 warning codes.
- Modify: `README.md` — add image-export + chunking examples.

- [ ] **Step 1: Inspect current §1.1 CompressOptions code block**

Read `docs/SPEC.md` lines 11-47 and compare to `pdf_smasher/types.py:CompressOptions` field-by-field. Identify every missing/mismatched field.

- [ ] **Step 2: Update `docs/SPEC.md` §1.1 CompressOptions**

Replace the code block at lines 11-47 with one that matches the current `types.py` declaration exactly, including all the new fields. Commentary on each field should be short (1 line).

- [ ] **Step 3: Update `docs/SPEC.md` §1.1 CompressReport**

Add `strategy_distribution: Mapping[str, int]` (line 63 of types.py) to the code block.

- [ ] **Step 4: Add `ProgressEvent` dataclass to §1.1**

Insert a new code block in §1.1 after CompressReport:

```python
@dataclass(frozen=True)
class ProgressEvent:
    """Structured progress event emitted during compress(). See
    pdf_smasher/types.py for the full definition."""
    phase: Literal[
        "triage", "triage_complete", "page_start", "page_done",
        "merge_start", "merge_complete", "verify_complete",
    ]
    message: str
    current: int = 0
    total: int = 0
    strategy: str | None = None
    ratio: float | None = None
    input_bytes: int | None = None
    output_bytes: int | None = None
    verifier_passed: bool | None = None
```

- [ ] **Step 5: Update `docs/SPEC.md` §1.2 compress() signature**

Update the function signature to:

```python
def compress(
    input_data: bytes,
    options: CompressOptions | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    only_pages: set[int] | None = None,
) -> tuple[bytes, CompressReport]:
    ...
```

- [ ] **Step 6: Update `docs/SPEC.md` §2.1 CLI flag list**

Replace the entire `Engine:` / `Output:` / etc. block with the flags actually declared in `pdf_smasher/cli/main.py:_parser`. Include each flag with its `help=` string.

- [ ] **Step 7: Update `docs/SPEC.md` §7.2 / §8.5 warning codes**

Append to the bulleted enum list:

- `verifier-skipped` — emitted once per job when options.skip_verify is True.
- `forkserver-preload-failed-<ExceptionType>` — emitted if set_forkserver_preload raised; workers fall back to per-fork module import.
- `chunks-exceed-cap-N-of-M` — emitted by the CLI chunk writer when one or more chunks exceed --max-output-mb because they contain a single oversize page.
- `stale-chunk-files-N` — emitted when pre-existing `{base}_NNN{ext}` files remain in the output dir after a new smaller chunk run.

- [ ] **Step 8: Update README.md**

Append a new section after the "What makes it different" list:

````markdown
## Output modes

HankPDF produces three output shapes out of the same `hankpdf` command:

**PDF (default):**
```bash
hankpdf in.pdf -o out.pdf
```

**Chunked PDF (for email attachment limits):**
```bash
hankpdf in.pdf -o out.pdf --max-output-mb 25
# writes out_001.pdf, out_002.pdf, ... if the merged output exceeds 25 MB
```

**Per-page image export (JPEG, PNG, or WebP):**
```bash
hankpdf in.pdf -o page.jpg --pages 1 --image-dpi 150 --jpeg-quality 80
hankpdf in.pdf -o dump.png --image-dpi 200
hankpdf in.pdf -o small.webp --pages 1-5 --webp-quality 70
```

Image export skips the MRC compression pipeline; each requested page
is rendered and saved as a standalone image.
````

- [ ] **Step 9: Build a docs-consistency check**

Optional but recommended: add a small test that imports `CompressOptions` and diffs its field names against the ones appearing in `docs/SPEC.md` §1.1. This catches future drift automatically.

Skip if time-boxed; otherwise add `tests/unit/test_docs_spec_consistency.py` with a test that grep-parses §1.1 and asserts every field in `dataclasses.fields(CompressOptions)` appears in the SPEC text.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m 'docs: sync SPEC.md and README.md with Phase-2b + DCR fixes

MEDIUM (DCR Wave 1). SPEC.md had drifted: §1.1 missing max_workers,
skip_verify, accept_drift, ProgressEvent, strategy_distribution;
still claimed ocr=True default. §2.1 listed removed flags, omitted
new ones. §8.5 warning codes missing verifier-skipped et al.

README.md got a new "Output modes" section showing chunking and
image-export examples that were completely undocumented.'
```

---

## Self-Review Checklist

Before handing off, verify:

1. **Spec coverage** — every DCR finding maps to a task:
   - Task 1 → module-relocation finding (Guardrails).
   - Task 2 → skip_verify fake "pass" (CRITICAL — multiple reviewers + pre-mortem).
   - Task 3 → empty --pages silent no-op (CRITICAL — QA reviewer).
   - Task 4 → image-export bypasses safety gates (CRITICAL — pre-mortem scenario 2b).
   - Task 5 → --image-dpi DoS (HIGH — QA reviewer).
   - Task 6 → --pages range DoS (MEDIUM — QA reviewer).
   - Task 7 → no progress, no per-page error context (HIGH — SRE reviewer).
   - Task 8 → OOM from buffering (HIGH — SRE reviewer).
   - Task 9 → chunk filename scheme + stale chunks (MEDIUM — Junior + SRE).
   - Task 10 → --max-output-mb silent ignore (MEDIUM — Junior).
   - Task 11 → --output-format / extension mismatch (LOW-MEDIUM — QA).
   - Task 12 → dead _WorkerInput.total_pages (LOW — Junior).
   - Task 13 → OCR pool lifetime (LOW — QA).
   - Task 14 → set_forkserver_preload silence (LOW — SRE).
   - Task 15 → SPEC.md drift + README missing examples (MEDIUM — Guardrails).

2. **Placeholder scan** — no "TBD", no "add appropriate error handling", no "write tests for the above" without test code. Verified.

3. **Type consistency** — `_enforce_input_policy` in Task 4 is referenced in Task 4 only. `_VerifierAggregator.skipped_result` in Task 2 is defined in Task 2 only. `iter_pages_as_images` in Task 8 matches the `render_pages_as_images` signature from Task 7 (same param set). `_parse_pages_spec` cap is `_MAX_PAGES_RANGE = 1_000_000` — used only in Task 6.

4. **Deferred review findings** — the DCR also flagged as LOW: encoding the VerifierStatus Literal into a union with non-numeric metrics, PNG lossless round-trip against the source raster, WebP `method` knob coverage, flag grouping in `--help` via `argparse.add_argument_group`. These are enhancement-bucket items not code-correctness blockers; intentionally omitted from this plan. Track in a separate follow-up.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-dcr-wave-1-remediation.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
