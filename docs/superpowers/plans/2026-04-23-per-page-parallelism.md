# Per-Page Parallelism Implementation Plan

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parallelize the per-page pipeline (rasterize → OCR → compose → verify) via `ProcessPoolExecutor` so wall time on a multi-page document scales ~linearly with physical cores. Each worker receives only its own single-page PDF slice, never the whole source.

**Architecture:** pre-split the input PDF once into N single-page byte streams (piggyback on the existing `input_page_bytes` measurement). Dispatch a worker function per page through `ProcessPoolExecutor`. Collect results into a dict keyed by page index; preserve original page order at merge time. Fall back to an inline serial loop when the pool would have ≤1 worker or page count < 4.

**Tech Stack:** `concurrent.futures.ProcessPoolExecutor`, `os.cpu_count()`. No new runtime deps.

---

## File Structure

- Modify: `pdf_smasher/__init__.py` — extract `_process_single_page`, add pool dispatch, `--max-workers`-equivalent kwarg.
- Modify: `pdf_smasher/cli/main.py` — add `--max-workers` CLI flag.
- Modify: `pdf_smasher/types.py` — add `max_workers: int = 0` to `CompressOptions` (0 = auto).
- Add: `tests/integration/test_parallel.py` — parallel-vs-serial equivalence gate.

## Design Contracts

**Default workers:** `max(1, (os.cpu_count() or 4) - 2)`. Reserves 1-2 cores for user's other work.

**Pool threshold:** below 4 selected pages, bypass the pool entirely. One-off serial is faster than pool startup.

**CLI knob:** `--max-workers N`
- `0` (default) = auto (`cpu_count - 2`, clamped ≥1)
- `1` = serial (no pool)
- `N>1` = exactly `N` workers

**Worker input:** `_WorkerInput` frozen dataclass (all fields serializable via the stdlib multiprocessing marshaling path — dataclasses, primitives, bytes only):
- `input_page_pdf: bytes` — single-page PDF extracted from source
- `page_index: int` — original 0-indexed position
- `page_size: tuple[float, float]` — (width_pt, height_pt)
- `source_dpi: int`
- `bg_target_dpi: int`
- `effective_bg_codec: str`
- `options: CompressOptions`
- `is_safe: bool`
- `lev_ceiling: float`
- `ssim_floor: float`

**Worker output:** `_PageResult` frozen dataclass (same constraint):
- `page_index: int`
- `composed_bytes: bytes`
- `strategy_name: str`
- `verdict: PageVerdict`
- `per_page_warnings: tuple[str, ...]` — warnings accumulated locally
- `input_bytes: int`
- `output_bytes: int`
- `ratio: float`

**Progress emission:** workers do NOT call `progress_callback`. They embed their events in the result. Parent emits on future resolution.

**Error handling:** `ProcessPoolExecutor.submit` returns futures. On first `.result()` that raises, cancel in-flight futures via `executor.shutdown(cancel_futures=True)` and re-raise. Successful results already collected are discarded.

**Output order:** results accumulated into `dict[int, _PageResult]`. At merge, iterate `sorted(selected_indices)` and pull from the dict.

**Shared mutable state:** `_JBIG2_CASCADE_STATE = threading.local()` — each process has its own module-level copy via fork/spawn, so this needs no changes. Verify.

---

## Task 1: Extract `_process_single_page` worker fn (no parallelism yet)

**Files:**
- Modify: `pdf_smasher/__init__.py`

- [ ] **Step 1: Write contract for `_WorkerInput` + `_PageResult` dataclasses**

```python
# In pdf_smasher/__init__.py, module scope:
from dataclasses import dataclass

@dataclass(frozen=True)
class _WorkerInput:
    input_page_pdf: bytes
    page_index: int           # 0-indexed in original PDF
    page_size: tuple[float, float]
    source_dpi: int
    bg_target_dpi: int
    effective_bg_codec: str
    options: CompressOptions
    is_safe: bool
    lev_ceiling: float
    ssim_floor: float


@dataclass(frozen=True)
class _PageResult:
    page_index: int
    composed_bytes: bytes
    strategy_name: str
    verdict: "PageVerdict"
    per_page_warnings: tuple[str, ...]
    input_bytes: int
    output_bytes: int
    ratio: float
```

- [ ] **Step 2: Extract `_process_single_page(worker_input) -> _PageResult`**

Move the per-page body of the existing loop (rasterize → OCR → classify → dispatch → verify → accumulate) verbatim into this function. Changes:

1. Replace `i` with `worker_input.page_index`, `width_pt, height_pt` from `worker_input.page_size`, etc.
2. Rasterize from `worker_input.input_page_pdf` (a 1-page PDF) with `page_index=0`.
3. Native-text extraction: open `worker_input.input_page_pdf` with pdfium, grab page 0.
4. Collect per-page warnings into a local `list[str]` that becomes `per_page_warnings` in the result.
5. Per-strategy verifier thresholds (`page_ssim_floor`, `page_lev_ceiling`, `page_tile_ssim_floor`) move inside the worker.
6. Worker does NOT call `_emit` — return values drive all events.
7. On exception inside the worker, let it propagate; `ProcessPoolExecutor` re-raises in parent.

- [ ] **Step 3: Rewrite `compress()` per-page loop to call the worker inline**

```python
for _pos, i in enumerate(_selected_indices, start=1):
    winput = _WorkerInput(
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
    )
    _emit("page_start", ..., current=_pos, total=len(_selected_indices))
    result = _process_single_page(winput)
    _accumulate_page_result(_pos, result, ...)
```

Where `_accumulate_page_result` merges the result into `page_pdfs_by_index`, `strategy_counts`, `warnings_list`, `verifier_agg`, and emits `page_done`.

- [ ] **Step 4: Extend the upfront PDF split to keep the bytes, not just the length**

The existing `input_page_bytes: dict[int, int]` becomes `single_page_pdfs: dict[int, bytes]`. Length is derived via `len(bytes)`. One extra dict, same pikepdf pass.

- [ ] **Step 5: Preserve output ordering**

`page_pdfs_by_index: dict[int, bytes]`. At merge time:
```python
for i in sorted(_selected_indices):
    page_pdfs.append(page_pdfs_by_index[i])
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: **192/192 pass.** Behavior must be identical — we've only moved code, not changed logic.

- [ ] **Step 7: Commit**

```bash
git commit -m "refactor: extract per-page worker fn (no parallelism yet — prep for pool)"
```

---

## Task 2: Wire `ProcessPoolExecutor` + `--max-workers` flag

**Files:**
- Modify: `pdf_smasher/__init__.py`
- Modify: `pdf_smasher/types.py`
- Modify: `pdf_smasher/cli/main.py`

- [ ] **Step 1: Add `max_workers` to CompressOptions**

```python
# pdf_smasher/types.py
# Concurrency (Phase 2c). 0 = auto (cpu_count - 2, min 1). 1 = serial.
# N > 1 = exactly N workers.
max_workers: int = 0
```

- [ ] **Step 2: Add `--max-workers` to the CLI**

```python
# pdf_smasher/cli/main.py in _parser():
p.add_argument(
    "--max-workers",
    type=int,
    default=0,
    help=(
        "Per-page parallelism. 0 (default) = auto (cpu_count-2, min 1). "
        "1 = serial. N>1 = exactly N workers. Each worker gets its own "
        "single-page PDF slice, never the whole source."
    ),
)
```

And in `_build_options()`:
```python
max_workers=args.max_workers,
```

- [ ] **Step 3: Resolve worker count in compress()**

```python
import os

def _resolve_worker_count(options: CompressOptions, n_pages: int) -> int:
    """Return the actual number of workers to use. 1 = serial path."""
    if options.max_workers == 1:
        return 1
    if options.max_workers >= 2:
        return min(options.max_workers, n_pages)
    # auto
    auto = max(1, (os.cpu_count() or 4) - 2)
    return min(auto, n_pages)
```

In `compress()`:
```python
n_workers = _resolve_worker_count(options, len(_selected_indices))
# Pool below 4 pages is slower than inline serial due to startup cost.
_PARALLEL_MIN_PAGES = 4
use_pool = n_workers > 1 and len(_selected_indices) >= _PARALLEL_MIN_PAGES
```

- [ ] **Step 4: Dispatch through pool**

```python
from concurrent.futures import ProcessPoolExecutor, as_completed

if use_pool:
    winputs = [
        _WorkerInput(...)
        for i in _selected_indices
    ]
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        future_to_pos = {
            ex.submit(_process_single_page, w): pos
            for pos, w in enumerate(winputs, start=1)
        }
        try:
            for fut in as_completed(future_to_pos):
                pos = future_to_pos[fut]
                result = fut.result()  # re-raises on worker failure
                _accumulate_page_result(pos, result, ...)
        except Exception:
            ex.shutdown(wait=False, cancel_futures=True)
            raise
else:
    # Inline serial (existing path from Task 1)
    for _pos, i in enumerate(_selected_indices, start=1):
        ...
```

- [ ] **Step 5: Drop `page_start` events in the parallel path**

When parallel, all workers start simultaneously — the "rasterizing pN" postfix doesn't mean anything specific. In parallel, only `page_done` events fire (tqdm counter still advances). In serial, both fire.

- [ ] **Step 6: Test the full suite still passes**

Run: `uv run pytest -q`
Expected: **all tests pass.** Tests currently use default options = 1 page in most cases, below the 4-page threshold, so they hit the serial path.

- [ ] **Step 7: Commit**

```bash
git commit -m "feat: parallel per-page compression via ProcessPoolExecutor"
```

---

## Task 3: Parallel-correctness integration test

**Files:**
- Add: `tests/integration/test_parallel.py`

- [ ] **Step 1: Write the equivalence test**

```python
"""Parallel-path equivalence: parallel output must match serial byte-for-byte."""

from __future__ import annotations

import io

import numpy as np
import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from pdf_smasher import CompressOptions, compress


def _make_multi_page_pdf(n_pages: int = 5) -> bytes:
    """Build an N-page PDF where each page has some content."""
    pdf = pikepdf.new()
    for page_i in range(n_pages):
        arr = np.full((2200, 1700, 3), 140, dtype=np.uint8)
        arr[300:1900, 200:1500] = 80
        img = Image.fromarray(arr)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("Arial.ttf", 60)
        except OSError:
            font = ImageFont.load_default(size=60)
        draw.text((300, 500), f"PAGE {page_i}", fill="black", font=font)
        pdf.add_blank_page(page_size=(612, 792))
        page = pdf.pages[-1]
        jbuf = io.BytesIO()
        img.save(jbuf, format="JPEG", quality=92, subsampling=0)
        xobj = pdf.make_stream(
            jbuf.getvalue(),
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
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.mark.integration
def test_parallel_serial_produce_equivalent_output() -> None:
    """Serial and parallel paths must produce byte-identical output PDFs.

    pikepdf writes with deterministic_id=True in our compose, so identical
    input + options = identical output bytes regardless of whether the pool
    was used.
    """
    pdf_in = _make_multi_page_pdf(n_pages=5)

    serial_bytes, serial_report = compress(
        pdf_in, options=CompressOptions(mode="fast", max_workers=1),
    )
    parallel_bytes, parallel_report = compress(
        pdf_in, options=CompressOptions(mode="fast", max_workers=0),  # auto
    )

    assert serial_report.pages == parallel_report.pages == 5
    assert serial_report.status == parallel_report.status == "ok"
    assert serial_bytes == parallel_bytes, (
        f"parallel output diverged from serial: "
        f"serial={len(serial_bytes):,} bytes, parallel={len(parallel_bytes):,} bytes"
    )


@pytest.mark.integration
def test_parallel_preserves_page_order() -> None:
    """Output page order must match input page order even when workers
    finish out of order. Check by rendering each output page and OCR-ing
    the embedded 'PAGE N' label."""
    import pypdfium2 as pdfium
    import pytesseract

    pdf_in = _make_multi_page_pdf(n_pages=5)
    pdf_out, _ = compress(pdf_in, options=CompressOptions(mode="fast", max_workers=4))
    doc = pdfium.PdfDocument(pdf_out)
    try:
        assert len(doc) == 5
        for expected_i in range(5):
            img = doc[expected_i].render(scale=150 / 72).to_pil()
            text = pytesseract.image_to_string(img)
            assert f"PAGE {expected_i}" in text, (
                f"page {expected_i} in output does not contain 'PAGE {expected_i}'; "
                f"got text={text[:100]!r}"
            )
    finally:
        doc.close()
```

- [ ] **Step 2: Run the new tests**

Run: `uv run pytest tests/integration/test_parallel.py -v`
Expected: both pass.

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git commit -m "test(parallel): serial/parallel equivalence + page-order integration tests"
```

---

## Self-Review Checklist

After Task 3:
- [ ] `--max-workers 1` path is identical to pre-refactor behavior (same tests green)
- [ ] `--max-workers 0` (auto) produces byte-identical output to `--max-workers 1`
- [ ] Page order preserved regardless of worker count
- [ ] Error in one worker cancels pool + propagates
- [ ] `_JBIG2_CASCADE_STATE` is process-local (unchanged by design — `threading.local()` in a new process starts fresh)
- [ ] Per-page warnings accumulated correctly (each worker returns its own list, parent merges)
- [ ] Progress events still fire in completion order (not page order) — FAIL ⚠ lines identify the page number so user isn't confused

## Non-Goals

- No per-worker memory cap (user said memory is not a concern).
- No shared-memory buffer for the source PDF (marshaling cost is acceptable).
- No GUI progress bar; stdout/stderr CLI only.
- No `--no-parallel` flag (equivalent to `--max-workers 1`).
