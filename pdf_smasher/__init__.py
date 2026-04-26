"""HankPDF — aggressive, safety-first PDF shrinker for scanned documents.

Public API surface:

    from pdf_smasher import compress, triage, CompressOptions, CompressReport

Engine implementation lives in submodules; this module re-exports the stable
contract. See docs/SPEC.md §1 for the full specification.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import IO, Any, Literal

import pikepdf

# Import for side effect: installs PIL.Image.MAX_IMAGE_PIXELS per SECURITY.md
# and docs/THREAT_MODEL.md. Must run before any other module that opens images
# via Pillow, so keep this import early in __init__.
from pdf_smasher import _pillow_hardening as _pillow_hardening
from pdf_smasher._version import __engine_version__, __version__
from pdf_smasher.exceptions import (
    CertifiedSignatureError,
    CompressError,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    EnvironmentError,  # noqa: A004 — part of our public error hierarchy
    MaliciousPDFError,
    OcrTimeoutError,
    OversizeError,
    PerPageTimeoutError,
    SignedPDFError,
    TotalTimeoutError,
)
from pdf_smasher.types import (
    BuildInfo,
    CompressOptions,
    CompressReport,
    ProgressEvent,
    TriageReport,
    VerifierResult,
)
from pdf_smasher.utils.text import format_page_list_short

__all__ = [
    "BuildInfo",
    "CertifiedSignatureError",
    "CompressError",
    "CompressOptions",
    "CompressReport",
    "ContentDriftError",
    "CorruptPDFError",
    "DecompressionBombError",
    "EncryptedPDFError",
    "EnvironmentError",
    "MaliciousPDFError",
    "OcrTimeoutError",
    "OversizeError",
    "PerPageTimeoutError",
    "ProgressEvent",
    "SignedPDFError",
    "TotalTimeoutError",
    "TriageReport",
    "VerifierResult",
    "__version__",
    "_enforce_input_policy",
    "compress",
    "compress_stream",
    "triage",
]


_JBIG2_CASCADE_STATE = threading.local()


_CHROMA_TO_PIL: dict[str, int] = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}

# Per-page ratio above which the tile-SSIM gate tightens to the safe floor.
# Chosen at 200x because TEXT_ONLY hits 100-500x legitimately via JBIG2 on
# dense text; anything above that on MIXED or PHOTO_ONLY is suspicious.
_ANOMALY_RATIO_THRESHOLD = 200.0

# When the verifier fails, list failing pages inline in the error message only
# if the count is at or below this; otherwise summarize.
_FAILING_PAGES_INLINE_LIMIT = 10

# Below this page count, the inline serial path is faster than pool startup
# overhead. See docs/superpowers/plans/2026-04-23-per-page-parallelism.md.
_PARALLEL_MIN_PAGES = 4

# Reserved cores left for the user's other work when auto-sizing the pool.
# Drop to 1 so we use N-1 of N cores: still headroom for the OS + user's
# other processes, but one more worker than before. Matters most on small
# core counts where cpu_count-2 was leaving too much idle.
_AUTO_WORKER_RESERVE = 1

# options.max_workers values: 0 = auto, 1 = serial, N >= this = N workers.
_MIN_EXPLICIT_WORKER_COUNT = 2


def _resolve_worker_count(options: CompressOptions, n_pages: int) -> int:
    """Return the actual number of workers for this run. 1 == serial path."""
    if options.max_workers == 1:
        return 1
    if options.max_workers >= _MIN_EXPLICIT_WORKER_COUNT:
        return min(options.max_workers, n_pages)
    auto = max(1, (os.cpu_count() or 4) - _AUTO_WORKER_RESERVE)
    return min(auto, n_pages)


def _init_worker() -> None:
    """ProcessPoolExecutor initializer. Pins each worker to single-threaded
    native libraries so N workers use exactly N cores, not N * cpu_count cores.

    Without this, Tesseract's OpenMP (and numpy BLAS + OpenCV) each try to
    use every core for themselves. Running multiple Tesseract subprocesses
    in parallel workers then creates N*cpu_count threads competing for
    cpu_count cores — context-switch thrash can fully eat the parallel
    speedup (sometimes making parallel slower than serial on the same box).
    """
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"


from dataclasses import dataclass as _dc  # noqa: E402 — adjacent to other module-level aliases


@_dc(frozen=True)
class _WorkerInput:
    """Single-page compression job. All fields serialize cleanly over the
    stdlib multiprocessing marshaling path (dataclasses, primitives, bytes).
    """

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


@_dc(frozen=True)
class _PageResult:
    """What a worker returns. `page_index` is the original position; the
    parent places results into a dict keyed by this and sorts at merge.
    """

    page_index: int
    composed_bytes: bytes
    strategy_name: str  # one of "text_only" / "photo_only" / "mixed"
    verdict: Any  # PageVerdict — engine.verifier.PageVerdict
    per_page_warnings: tuple[str, ...]
    input_bytes: int
    output_bytes: int
    ratio: float
    # Wall-clock time INSIDE the worker. Compare sum(worker_wall_ms) vs total
    # parent wall to diagnose parallelism: if parallel, sum >> wall; if
    # serial/contention, sum ≈ wall.
    worker_wall_ms: int = 0


def _format_verifier_failing_pages(ids: tuple[int, ...], limit: int = 10) -> str:
    """Format failing-page IDs for a warning code: comma-separated ints,
    no brackets, no spaces, capped at ``limit`` with a ``+N`` suffix for
    longer lists. Keeps warning codes grep-friendly and bounded length.
    """
    sorted_ids = sorted(ids)
    if len(sorted_ids) <= limit:
        return ",".join(str(n) for n in sorted_ids)
    head = sorted_ids[:limit]
    remaining = len(sorted_ids) - limit
    return f"{','.join(str(n) for n in head)}+{remaining}"


def _build_passthrough_report(
    input_data: bytes,
    pages: int,
    wall_ms: int,
    reason: str,
    warning_code: str,
    *,
    correlation_id: str | None = None,
) -> CompressReport:
    """Construct a CompressReport for a passthrough return (input unchanged).

    Used by :func:`compress` when one of the ``CompressOptions`` thresholds
    (min_input_mb, min_ratio) short-circuits the pipeline — the output
    equals the input, verifier is marked "skipped" (nothing to compare),
    and a kebab-case warning code names the specific gate that tripped.

    Delegates to ``_VerifierAggregator().skipped_result()`` so there's a
    single source of truth for the fail-closed sentinel policy. Without
    this, the hand-rolled VerifierResult here and the one returned by
    ``skipped_result()`` can drift (ocr_levenshtein=0.0 vs 1.0 — one is
    fail-open, one fail-closed).
    """
    import hashlib

    # Local import to avoid circular import at module load: engine.verifier
    # imports from pdf_smasher.types, and this module re-exports from types.
    from pdf_smasher.audit import resolve_build_info
    from pdf_smasher.engine.verifier import _VerifierAggregator
    from pdf_smasher.types import _new_correlation_id

    sha = hashlib.sha256(input_data).hexdigest()
    return CompressReport(
        status="passed_through",
        exit_code=2,  # EXIT_NOOP_PASSTHROUGH per cli.main
        input_bytes=len(input_data),
        output_bytes=len(input_data),
        ratio=1.0,
        pages=pages,
        wall_time_ms=wall_ms,
        engine="mrc",
        engine_version=__engine_version__,
        verifier=_VerifierAggregator().skipped_result(),
        input_sha256=sha,
        output_sha256=sha,  # same bytes → same hash
        canonical_input_sha256=None,
        warnings=(warning_code,),
        reason=reason,
        build_info=resolve_build_info(),
        correlation_id=correlation_id if correlation_id is not None else _new_correlation_id(),
    )


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
        msg = "input carries a certifying signature; --allow-certified-invalidation required"
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


def _mrc_compose(
    raster: Any,
    mask: Any,
    width_pt: float,
    height_pt: float,
    bg_target_dpi: int,
    source_dpi: int,
    *,
    bg_codec: Literal["jpeg", "jpeg2000"] = "jpeg",
    bg_jpeg_quality: int = 45,
    bg_subsampling: int = 0,
    force_grayscale: bool = False,
) -> bytes:
    """MRC composition helper (Task 4a). Caller decides bg_color_mode via raster check."""
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
    bg_color_mode: Literal["rgb", "grayscale"] = (
        "grayscale" if (force_grayscale or is_effectively_monochrome(raster)) else "rgb"
    )
    return compose_mrc_page(
        foreground=fg.image,
        foreground_color=fg.ink_color,
        mask=mask,
        background=bg,
        page_width_pt=width_pt,
        page_height_pt=height_pt,
        bg_color_mode=bg_color_mode,
        bg_codec=bg_codec,
        bg_jpeg_quality=bg_jpeg_quality,
        bg_subsampling=bg_subsampling,
    )


def _process_single_page(winput: _WorkerInput) -> _PageResult:
    """Run the full per-page pipeline on a single-page PDF slice.

    This function runs in a worker process when parallelism is enabled, or
    inline in the main process for small jobs / serial mode. It takes ONLY
    its own single-page PDF (not the whole source) so worker memory stays
    bounded to one page's raster + compose buffers.

    Imports are function-local so worker processes don't pay the engine
    import cost until they actually run (pool startup is cheap).
    """
    import contextlib

    import numpy as np

    from pdf_smasher.engine.compose import (
        compose_photo_only_page,
        compose_text_only_page,
    )
    from pdf_smasher.engine.foreground import (
        detect_paper_color,
        extract_foreground,
        is_effectively_monochrome,
    )
    from pdf_smasher.engine.mask import build_mask
    from pdf_smasher.engine.ocr import tesseract_word_boxes
    from pdf_smasher.engine.rasterize import rasterize_page
    from pdf_smasher.engine.strategy import PageStrategy, classify_page
    from pdf_smasher.engine.text_layer import add_text_layer
    from pdf_smasher.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
        _page_has_color,
        verify_single_page,
    )

    _worker_t0 = time.monotonic()
    i = winput.page_index
    width_pt, height_pt = winput.page_size
    options = winput.options
    is_safe = winput.is_safe
    lev_ceiling = winput.lev_ceiling
    ssim_floor = winput.ssim_floor
    warnings: list[str] = []

    # --- Rasterize input ---
    raster = rasterize_page(
        winput.input_page_pdf, page_index=0, dpi=winput.source_dpi, password=None
    )

    # Input OCR is needed when:
    #   - options.ocr (for add_text_layer positioning), OR
    #   - verifier runs (for drift comparison)
    # When BOTH are disabled (--skip-verify --no-ocr), skip Tesseract entirely.
    # Tesseract is subprocess-based and releases the GIL during wait, so we
    # kick input OCR off in a background thread NOW — it runs concurrently
    # with mask/classify/compose and is awaited when we need the text.
    need_input_ocr = bool(options.ocr) or not options.skip_verify
    _input_ocr_future: Any = None
    word_boxes: list[Any] = []
    ocr_text: str = ""
    # ExitStack guarantees the OCR ThreadPoolExecutor's __exit__ fires on
    # any path out of this block (happy return OR exception), which drains
    # in-flight tesseract subprocess threads instead of leaking them.
    _ocr_pool: ThreadPoolExecutor | None = None
    with contextlib.ExitStack() as _ocr_stack:
        if need_input_ocr:
            _ocr_pool = ThreadPoolExecutor(max_workers=2)  # 1 for input, 1 for output
            # Register an explicit shutdown callback that cancels pending
            # futures and skips waiting. The default ExitStack + `with`
            # on a ThreadPoolExecutor invokes shutdown(wait=True,
            # cancel_futures=False) — if a Tesseract subprocess is wedged
            # (timeout kwarg saves us for normal hangs, but signals like
            # SIGSTOP can defeat it), we'd block forever on worker-pool
            # exit. cancel_futures=True tells any queued-but-unstarted
            # tasks to abandon, and wait=False doesn't block.
            # Pytesseract's timeout kwarg already kills subprocesses on
            # overrun, so in-flight work almost always completes.
            # Pin to the local binding so the lambda captures a non-None
            # ThreadPoolExecutor (mypy can't narrow across the lambda).
            _pool = _ocr_pool
            _ocr_stack.callback(
                lambda: _pool.shutdown(wait=False, cancel_futures=True),
            )
            _input_ocr_future = _ocr_pool.submit(
                tesseract_word_boxes,
                raster,
                language=options.ocr_language,
                timeout_seconds=options.per_page_timeout_seconds,
            )

        # Source-truth: prefer native PDF text layer when present.
        # (Still cheap even with skip_verify; just a textpage read.)
        from pypdfium2 import PdfDocument as _Pdfium

        _doc = _Pdfium(winput.input_page_pdf)
        try:
            _tp = _doc[0].get_textpage()
            try:
                _native_text = _tp.get_text_range()
            finally:
                _tp.close()
        finally:
            _doc.close()

        def _await_input_ocr() -> None:
            """Populate word_boxes + ocr_text from the background OCR future.
            No-op if already resolved or not needed."""
            nonlocal word_boxes, ocr_text
            if _input_ocr_future is None or word_boxes:
                return
            word_boxes = _input_ocr_future.result()
            ocr_text = " ".join(b.text for b in word_boxes)

        # If native text is present we can skip waiting on OCR for the verifier
        # ground-truth (native wins anyway). But we still need word_boxes later
        # if options.ocr is set (for text-layer positioning).
        if _native_text and _native_text.strip():
            input_ocr_text = _native_text.strip()
        else:
            _await_input_ocr()
            input_ocr_text = ocr_text

        # --- Mask + classify ---
        mask = build_mask(raster)
        mask_arr = np.asarray(mask.convert("1"), dtype=bool)
        mask_coverage = float(mask_arr.sum()) / max(1, mask_arr.size)
        strategy = classify_page(raster, mask_coverage_fraction=mask_coverage)

        if options.force_monochrome and _page_has_color(raster):
            warnings.append(f"page-{i + 1}-color-detected-in-monochrome-mode")

        if strategy == PageStrategy.ALREADY_OPTIMIZED:
            msg = (
                f"page {i + 1}: classify_page returned ALREADY_OPTIMIZED but "
                "compress() has no handler for this value."
            )
            raise AssertionError(msg)

        # --- Strategy dispatch ---
        if strategy == PageStrategy.TEXT_ONLY:
            if not options.force_monochrome and not is_effectively_monochrome(raster):
                warnings.append(f"page-{i + 1}-text-only-demoted-to-mixed-color-detected")
                strategy = PageStrategy.MIXED
                _JBIG2_CASCADE_STATE.tripped = False
                composed = _mrc_compose(
                    raster,
                    mask,
                    width_pt,
                    height_pt,
                    winput.bg_target_dpi,
                    winput.source_dpi,
                    bg_codec=winput.effective_bg_codec,  # type: ignore[arg-type]
                    bg_jpeg_quality=options.target_color_quality,
                    bg_subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
                    force_grayscale=options.force_monochrome,
                )
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
            _photo_bg_color_mode: Literal["rgb", "grayscale"] = (
                "grayscale"
                if (options.force_monochrome or is_effectively_monochrome(raster))
                else "rgb"
            )
            composed = compose_photo_only_page(
                raster=raster,
                page_width_pt=width_pt,
                page_height_pt=height_pt,
                target_dpi=options.photo_target_dpi,
                bg_color_mode=_photo_bg_color_mode,
                bg_codec=winput.effective_bg_codec,  # type: ignore[arg-type]
                jpeg_quality=options.target_color_quality,
                subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
            )
        else:  # MIXED
            _JBIG2_CASCADE_STATE.tripped = False
            composed = _mrc_compose(
                raster,
                mask,
                width_pt,
                height_pt,
                winput.bg_target_dpi,
                winput.source_dpi,
                bg_codec=winput.effective_bg_codec,  # type: ignore[arg-type]
                bg_jpeg_quality=options.target_color_quality,
                bg_subsampling=_CHROMA_TO_PIL[options.bg_chroma_subsampling],
                force_grayscale=options.force_monochrome,
            )
            if getattr(_JBIG2_CASCADE_STATE, "tripped", False):
                warnings.append(f"page-{i + 1}-jbig2-fallback-to-flate")

        if options.ocr:
            _await_input_ocr()
            composed = add_text_layer(
                composed,
                page_index=0,
                word_boxes=word_boxes,
                raster_width_px=raster.size[0],
                raster_height_px=raster.size[1],
                page_width_pt=width_pt,
                page_height_pt=height_pt,
            )

        # --- Streaming verify ---
        # When skip_verify is set, synthesize a trivially-passing verdict and
        # don't re-rasterize / re-OCR the output. Saves 2-5 s/page (Tesseract
        # runs twice per page inside the verifier otherwise).
        if options.skip_verify:
            from pdf_smasher.engine.verifier import PageVerdict as _PageVerdict

            verdict = _PageVerdict(
                page_index=-1,
                passed=True,  # don't add to failing_pages
                lev=1.0,  # sentinel: max drift
                ssim_global=0.0,
                ssim_tile_min=0.0,
                digits_match=False,
                color_preserved=False,
            )
        else:
            output_raster = rasterize_page(
                composed, page_index=0, dpi=winput.source_dpi, password=None
            )
            # Kick off output OCR in parallel with awaiting the input OCR (if
            # we haven't already needed it). Two tesseract subprocesses run
            # concurrently inside this worker — each uses 1 thread (OMP pinned)
            # so the pair still fits in 1 CPU core's worth of work, but they
            # overlap for ~40% wall-time reduction on the OCR phase.
            if _ocr_pool is not None:
                _output_ocr_future = _ocr_pool.submit(
                    tesseract_word_boxes,
                    output_raster,
                    language=options.ocr_language,
                    timeout_seconds=options.per_page_timeout_seconds,
                )
            else:
                _output_ocr_future = None
            # Resolve input OCR (may already be done if native text wasn't present).
            _await_input_ocr()
            # Reassign in case native text was used — verifier needs input_ocr_text
            # from our source-truth path above; here we need the RAW tesseract
            # output for comparison against RAW tesseract on the output.
            # Note: we use `input_ocr_text` (source-truth preferred) as the ground
            # truth, but compare against `output_ocr_text` which is always raw OCR.
            if _output_ocr_future is not None:
                _out_wb = _output_ocr_future.result()
            else:
                _out_wb = tesseract_word_boxes(
                    output_raster,
                    language=options.ocr_language,
                    timeout_seconds=options.per_page_timeout_seconds,
                )
            output_ocr_text = " ".join(b.text for b in _out_wb)
            per_page_input_estimate = raster.width * raster.height * 3
            per_page_pixel_ratio = per_page_input_estimate / max(1, len(composed))
            anomalous = (
                per_page_pixel_ratio > _ANOMALY_RATIO_THRESHOLD
                and strategy != PageStrategy.TEXT_ONLY
            )
            if strategy == PageStrategy.MIXED:
                page_tile_ssim_floor = (
                    _DEFAULT_TILE_SSIM_FLOOR_SAFE
                    if (anomalous or is_safe)
                    else _DEFAULT_TILE_SSIM_FLOOR_STANDARD
                )
                page_ssim_floor = ssim_floor
                page_lev_ceiling = lev_ceiling
            elif strategy == PageStrategy.PHOTO_ONLY:
                page_tile_ssim_floor = -1.0
                page_ssim_floor = 0.5
                page_lev_ceiling = 1.0
            else:
                page_tile_ssim_floor = -1.0
                page_ssim_floor = ssim_floor
                page_lev_ceiling = lev_ceiling
            if anomalous:
                warnings.append(
                    f"page-{i + 1}-anomalous-ratio-{per_page_pixel_ratio:.0f}x-safe-verify",
                )
            verdict = verify_single_page(
                input_raster=raster,
                output_raster=output_raster,
                input_ocr_text=input_ocr_text,
                output_ocr_text=output_ocr_text,
                lev_ceiling=page_lev_ceiling,
                ssim_floor=page_ssim_floor,
                tile_ssim_floor=page_tile_ssim_floor,
                check_color_preserved=not options.force_monochrome,
            )

    in_bytes = len(winput.input_page_pdf)
    out_bytes = len(composed)
    true_ratio = in_bytes / max(1, out_bytes)

    return _PageResult(
        page_index=i,
        composed_bytes=composed,
        strategy_name=strategy.name.lower(),
        verdict=verdict,
        per_page_warnings=tuple(warnings),
        input_bytes=in_bytes,
        output_bytes=out_bytes,
        ratio=true_ratio,
        worker_wall_ms=int((time.monotonic() - _worker_t0) * 1000),
    )


def _extract_ground_truth_text(
    pdf_bytes: bytes,
    page_index: int,
    fallback_ocr_text: str,
) -> str:
    """Return the native text layer for `page_index`, or `fallback_ocr_text`.

    Prefers the native PDF text layer when present and non-empty; falls back
    to the pre-computed OCR text when the page has no native layer (e.g., a
    scanned PDF with no embedded text). Prevents both-OCR-wrong scenarios
    where Tesseract misreads a digit on BOTH input and output (Task 0.5).
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
                    return str(native).strip()
            finally:
                _tp.close()
        finally:
            _doc.close()
    except Exception:  # noqa: BLE001, S110 — best-effort native-text probe; any error → fall back to OCR.
        pass
    return fallback_ocr_text


def triage(input_data: bytes, *, password: str | None = None) -> TriageReport:
    """Cheap structural scan. Never decodes image streams. See SPEC.md §4."""
    from pdf_smasher.engine.triage import triage as _triage

    return _triage(input_data, password=password)


def compress(
    input_data: bytes,
    options: CompressOptions | None = None,
    *,
    progress_callback: Callable[[ProgressEvent], None] | None = None,
    only_pages: set[int] | None = None,
    correlation_id: str | None = None,
) -> tuple[bytes, CompressReport]:
    """Compress a PDF.

    Full pipeline per SPEC.md §1: triage → sanitize → recompress → verify →
    report. Raises one of the :class:`CompressError` subclasses on refusal
    or drift.

    ``progress_callback`` is an optional ``fn(event: ProgressEvent) -> None``
    hook invoked at pipeline milestones (triage, per-page start/done,
    merge, verify). The CLI drives a tqdm progress bar from these events.
    Events carry no PHI — only phase, page indices, strategy names, byte
    counts, and ratios.

    ``only_pages`` (1-indexed page numbers) restricts processing to a
    subset of pages. The output PDF contains only the selected pages in
    their original order. Pages outside the set are skipped entirely —
    no rasterization, no OCR, no verification. Useful for smoke tests.

    ``correlation_id`` (Wave 5 / C2) is stamped into the returned
    :class:`CompressReport` so callers can tie this report to the stderr
    lines their own logger emits around the invocation. If omitted, a
    fresh UUID4 is generated. Library callers who drive multiple pages
    through one logger should pass the same id they prefix stderr with.
    """
    t0 = time.monotonic()
    options = options or CompressOptions()

    def _check_total_timeout(phase: str) -> None:
        """Raise TotalTimeoutError if the cumulative wall-clock since t0
        exceeds options.total_timeout_seconds.

        Called between pipeline phases (post-triage, post-per-page, post-
        merge, post-verify). ``total_timeout_seconds=0`` disables the
        watchdog entirely.
        """
        budget = options.total_timeout_seconds
        if budget <= 0:
            return
        elapsed = time.monotonic() - t0
        if elapsed > budget:
            msg = f"total_timeout_seconds={budget} exceeded after {phase}: {elapsed:.2f}s elapsed"
            raise TotalTimeoutError(msg)

    def _emit(
        phase: str,
        message: str,
        *,
        current: int = 0,
        total: int = 0,
        strategy: str | None = None,
        ratio: float | None = None,
        input_bytes: int | None = None,
        output_bytes: int | None = None,
        verifier_passed: bool | None = None,
    ) -> None:
        if progress_callback is not None:
            progress_callback(
                ProgressEvent(
                    phase=phase,  # type: ignore[arg-type]
                    message=message,
                    current=current,
                    total=total,
                    strategy=strategy,
                    ratio=ratio,
                    input_bytes=input_bytes,
                    output_bytes=output_bytes,
                    verifier_passed=verifier_passed,
                ),
            )

    # GUARD: legal_codec_profile (CCITT G4) is reserved for a later phase.
    # Placed before triage so the error is actionable even on empty input.
    if options.legal_codec_profile:
        msg = (
            "legal_codec_profile (CCITT G4 fallback) is not implemented in this "
            "build. Use legal_codec_profile=None and --engine mrc for Phase-2b "
            "outputs; tracked for a later phase."
        )
        raise NotImplementedError(msg)

    # Lazy imports of engine modules so ``from pdf_smasher import CompressOptions``
    # doesn't pay the startup cost of loading pdfium / OpenCV / Tesseract.
    from pdf_smasher.engine.canonical import canonical_input_sha256
    from pdf_smasher.engine.triage import triage as _triage
    from pdf_smasher.engine.verifier import _VerifierAggregator

    # --- Triage ---
    _emit("triage", f"triage: {len(input_data):,} bytes input")
    tri = _triage(input_data, password=options.password)

    # Validate + apply only_pages filter.
    if only_pages is not None:
        if not only_pages:
            msg = "only_pages is empty — no pages selected"
            raise CompressError(msg)
        out_of_range = [p for p in only_pages if p < 1 or p > tri.pages]
        if out_of_range:
            msg = (
                f"only_pages requested {format_page_list_short(out_of_range)} "
                f"but input has {tri.pages} pages"
            )
            raise CompressError(msg)
        # Convert to 0-indexed set for internal use, keep sorted order for output.
        _selected_indices = sorted(p - 1 for p in only_pages)
    else:
        _selected_indices = list(range(tri.pages))

    _emit(
        "triage_complete",
        (
            f"triage complete: {tri.pages} pages, classification={tri.classification}, "
            f"encrypted={tri.is_encrypted}, signed={tri.is_signed}"
            + (
                f" — processing {len(_selected_indices)}/{tri.pages} pages"
                if only_pages is not None
                else ""
            )
        ),
        total=len(_selected_indices),
    )

    _enforce_input_policy(tri, options, input_data)
    _check_total_timeout("triage")

    # --- Passthrough: min_input_mb floor ---
    # If the input is below the configured minimum size, return it
    # unchanged. The MRC pipeline's per-page overhead (~2-3 s/page even
    # in --mode fast) isn't worth the ratio gain on small files; batch
    # operators set this to skip anything already below their quota.
    # Must fire BEFORE the expensive pdfium open / page-split phase.
    input_mb = len(input_data) / (1024 * 1024)
    if options.min_input_mb > 0 and input_mb < options.min_input_mb:
        wall_ms = int((time.monotonic() - t0) * 1000)
        reason = f"input {input_mb:.3f} MB below min_input_mb={options.min_input_mb} MB"
        return input_data, _build_passthrough_report(
            input_data,
            pages=tri.pages,
            wall_ms=wall_ms,
            reason=reason,
            warning_code="passthrough-min-input-mb",
            correlation_id=correlation_id,
        )

    # --- Per-page recompress ---
    source_dpi = 200 if options.mode == "fast" else 300
    bg_target_dpi = options.target_bg_dpi
    is_safe = options.mode == "safe"

    import pypdfium2 as pdfium

    # We need page dimensions — open with pdfium once for sizing.
    pdf_dims = pdfium.PdfDocument(input_data, password=options.password)
    page_sizes: list[tuple[float, float]] = []
    try:
        for i in range(tri.pages):
            w, h = pdf_dims[i].get_size()
            page_sizes.append((float(w), float(h)))
    finally:
        pdf_dims.close()

    # Split the source PDF into per-page byte slices. Each worker (parallel
    # or serial) gets only its own 1-page PDF, never the whole source.
    # Byproduct: length → per-page input bytes used for honest ratio display.
    single_page_pdfs: dict[int, bytes] = {}
    with pikepdf.open(io.BytesIO(input_data), password=options.password or "") as _src_split:
        for i in _selected_indices:
            _single = pikepdf.new()
            try:
                _single.pages.append(_src_split.pages[i])
                _buf = io.BytesIO()
                _single.save(_buf, linearize=False)
                single_page_pdfs[i] = _buf.getvalue()
            finally:
                _single.close()

    lev_ceiling = 0.02 if is_safe else 0.05
    ssim_floor = 0.92

    warnings_list: list[str] = []
    if options.skip_verify:
        warnings_list.append("verifier-skipped")
    if shutil.which("jbig2") is None:
        warnings_list.append("jbig2enc-unavailable-using-flate-fallback")

    # Fast mode forces JPEG on the bg path: JPEG2000 via Pillow/OpenJPEG is
    # ~1-2 s/page at 300 DPI (+3-6 min on a 200-page doc). Users who asked
    # for fast explicitly traded ratio for speed — don't silently undo that.
    # Emit a warning so a user who set bg_codec=jpeg2000 isn't surprised.
    effective_bg_codec = options.bg_codec
    if options.mode == "fast" and options.bg_codec == "jpeg2000":
        warnings_list.append("bg-codec-jpeg2000-demoted-fast-mode")
        effective_bg_codec = "jpeg"
    verifier_agg = _VerifierAggregator()
    strategy_counts: dict[str, int] = {
        "text_only": 0,
        "photo_only": 0,
        "mixed": 0,
        "already_optimized": 0,
    }

    # Page results accumulate by original index; output merges in sorted order
    # regardless of completion order (matters when parallel).
    page_pdfs_by_index: dict[int, bytes] = {}

    _worker_wall_ms_total: list[int] = []

    def _merge_result(pos: int, result: _PageResult) -> None:
        """Accumulate a worker result into parent state + emit page_done."""
        page_pdfs_by_index[result.page_index] = result.composed_bytes
        warnings_list.extend(result.per_page_warnings)
        verifier_agg.merge(result.page_index, result.verdict)
        strategy_counts[result.strategy_name] += 1
        _worker_wall_ms_total.append(result.worker_wall_ms)
        # Under skip_verify the worker synthesizes a trivially-passing
        # verdict so it can still be aggregated; don't let that leak
        # into the progress event as "verifier=pass". Emit None (tri-
        # state) and surface "skipped" in the human-readable message.
        if options.skip_verify:
            v_passed: bool | None = None
            v_label = "skipped"
        else:
            v_passed = result.verdict.passed
            v_label = "pass" if result.verdict.passed else "fail"
        _emit(
            "page_done",
            f"page {result.page_index + 1}/{tri.pages} done: strategy={result.strategy_name}, "
            f"{result.input_bytes:,}→{result.output_bytes:,} bytes "
            f"({result.ratio:.2f}x), worker={result.worker_wall_ms}ms, "
            f"verifier={v_label}",
            current=pos,
            total=len(_selected_indices),
            strategy=result.strategy_name,
            ratio=result.ratio,
            input_bytes=result.input_bytes,
            output_bytes=result.output_bytes,
            verifier_passed=v_passed,
        )

    # Build worker inputs once.
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
        )
        for i in _selected_indices
    ]

    n_workers = _resolve_worker_count(options, len(_selected_indices))
    use_pool = n_workers > 1 and len(_selected_indices) >= _PARALLEL_MIN_PAGES

    def _page_error_context(page_index: int, exc: BaseException) -> str:
        return f"compression failed on page {page_index + 1}/{tri.pages}: {exc}"

    if use_pool:
        # Executor choice:
        #   - pdfium is NOT thread-safe (pypdfium2 #303); threads crash
        #     with heap corruption under concurrent access. So threads
        #     are off the table as a default.
        #   - macOS's `spawn` start method makes every worker re-import
        #     numpy/OpenCV/pikepdf from scratch (~2-3 s each). On small
        #     jobs the spawn cost dominates actual compute and
        #     parallelism looks like a regression.
        #   - `forkserver` starts ONE small server process up front and
        #     forks workers from it on demand. With `set_forkserver_preload`
        #     our heavy modules are imported ONCE in the server, so every
        #     worker fork is ~10-50 ms. Safer than raw `fork` (no
        #     fork-after-threads hazard) and much faster than pure spawn.
        #   - Windows has no forkserver — falls back to spawn there.
        #
        # Escape hatch: HANKPDF_POOL=thread opts into ThreadPoolExecutor
        # (faster on toy cases but liable to crash on pdfium access).
        import multiprocessing as _mp

        _pool_kind = os.environ.get("HANKPDF_POOL", "process").lower()
        _init_worker()  # also pin parent's OMP/BLAS for consistency
        _ex_kwargs: dict[str, Any] = {"max_workers": n_workers}
        if _pool_kind == "thread":
            executor_cls: type = ThreadPoolExecutor
            label = "thread (UNSAFE: pdfium not thread-safe, may crash)"
        else:
            executor_cls = ProcessPoolExecutor
            _ex_kwargs["initializer"] = _init_worker
            available = _mp.get_all_start_methods()
            chosen_method = "forkserver" if "forkserver" in available else "spawn"
            ctx = _mp.get_context(chosen_method)
            if chosen_method == "forkserver":
                try:
                    ctx.set_forkserver_preload(
                        [
                            "numpy",
                            "PIL",
                            "PIL.Image",
                            "cv2",
                            "pikepdf",
                            "pypdfium2",
                            "pytesseract",
                            "skimage",
                            "skimage.metrics",
                            "pdf_smasher.engine.rasterize",
                            "pdf_smasher.engine.ocr",
                            "pdf_smasher.engine.mask",
                            "pdf_smasher.engine.strategy",
                            "pdf_smasher.engine.compose",
                            "pdf_smasher.engine.foreground",
                            "pdf_smasher.engine.text_layer",
                            "pdf_smasher.engine.verifier",
                            "pdf_smasher.engine.background",
                        ]
                    )
                except (ValueError, RuntimeError) as _e:
                    # set_forkserver_preload can raise ValueError/RuntimeError
                    # if a listed module instantiates mp objects at import time.
                    # Workers still function, but each re-imports the heavy
                    # module chain (numpy/cv2/pikepdf ~ 2-3 s each) — a
                    # significant silent regression. Surface it as a warning
                    # so users/ops can grep for it.
                    warnings_list.append(
                        f"forkserver-preload-failed-{type(_e).__name__}",
                    )
            _ex_kwargs["mp_context"] = ctx
            label = f"process/{chosen_method}"
        _emit(
            "triage",
            f"parallel dispatch: {n_workers} workers ({label}) x {len(winputs)} pages",
            total=len(winputs),
        )
        # In the parallel path we do NOT emit per-page page_start events —
        # workers all start simultaneously so the "rasterizing pN" label
        # doesn't mean anything. Only page_done fires (from completion order).
        with executor_cls(**_ex_kwargs) as ex:
            future_to_winput: dict[Any, _WorkerInput] = {
                ex.submit(_process_single_page, w): w for w in winputs
            }
            # `as_completed` iterates in completion order. `_pos` is 1-indexed
            # completion position, which is what tqdm wants for the progress bar.
            # Per-page timeout: we wait at most per_page_timeout_seconds * n_pages
            # for ALL futures to complete (a proxy for strict per-page — true
            # per-page is impossible with as_completed since workers start
            # together). If the total budget expires, we cancel and raise
            # PerPageTimeoutError naming the laggards.
            _per_page_budget = options.per_page_timeout_seconds
            _parallel_total_budget = _per_page_budget * max(1, len(winputs))
            try:
                _completed_iter = as_completed(
                    future_to_winput,
                    timeout=_parallel_total_budget,
                )
                for _pos, fut in enumerate(_completed_iter, start=1):
                    w = future_to_winput[fut]
                    try:
                        result = fut.result()
                    except KeyboardInterrupt:
                        ex.shutdown(wait=False, cancel_futures=True)
                        raise
                    except AssertionError, CompressError:
                        ex.shutdown(wait=False, cancel_futures=True)
                        raise
                    except Exception as e:
                        ex.shutdown(wait=False, cancel_futures=True)
                        msg = _page_error_context(w.page_index, e)
                        raise CompressError(msg) from e
                    _merge_result(_pos, result)
            except FuturesTimeoutError as e:
                ex.shutdown(wait=False, cancel_futures=True)
                _pending = [
                    fw.page_index + 1 for fut, fw in future_to_winput.items() if not fut.done()
                ]
                _pending_display = _format_verifier_failing_pages(
                    tuple(_pending),
                    limit=10,
                )
                msg = (
                    f"per-page timeout ({_per_page_budget}s x {len(winputs)} pages) "
                    f"exceeded; {len(_pending)} page(s) still pending: "
                    f"{_pending_display}"
                )
                raise PerPageTimeoutError(msg) from e
            except BaseException:  # pragma: no cover — cancellation path
                ex.shutdown(wait=False, cancel_futures=True)
                raise
    else:
        # Serial path: dispatch each page through a 1-worker ThreadPoolExecutor
        # so we can enforce per_page_timeout_seconds via future.result(timeout=).
        # The thread still runs in this process (no IPC overhead). Pytesseract's
        # own timeout kwarg kills the Tesseract subprocess on overrun, so the
        # thread unblocks — the future-level timeout is belt-and-suspenders for
        # non-Tesseract hangs (numpy/opencv loops without GIL release).
        _per_page_budget = options.per_page_timeout_seconds
        with ThreadPoolExecutor(max_workers=1) as _serial_ex:
            for _pos, w in enumerate(winputs, start=1):
                _emit(
                    "page_start",
                    f"page {w.page_index + 1}/{tri.pages}: rasterizing + OCR ({source_dpi} DPI)",
                    current=_pos,
                    total=len(winputs),
                )
                _fut = _serial_ex.submit(_process_single_page, w)
                try:
                    result = _fut.result(timeout=_per_page_budget)
                except FuturesTimeoutError as e:
                    # Cancel the executor before raising so the zombie thread
                    # doesn't keep consuming CPU. The Tesseract subprocess was
                    # already killed by pytesseract's timeout (which is <=
                    # this budget), so the thread unblocks soon.
                    _serial_ex.shutdown(wait=False, cancel_futures=True)
                    msg = (
                        f"page {w.page_index + 1}/{tri.pages} exceeded "
                        f"per_page_timeout_seconds={_per_page_budget}"
                    )
                    raise PerPageTimeoutError(msg) from e
                except KeyboardInterrupt:
                    page_pdfs_by_index.clear()
                    raise
                except AssertionError, CompressError:
                    raise
                except Exception as e:
                    msg = _page_error_context(w.page_index, e)
                    raise CompressError(msg) from e
                _merge_result(_pos, result)

    # Parallelism diagnostic. Compare the sum of per-worker wall times to
    # the pages-phase wall time. If workers truly parallelize, the sum is
    # much bigger than the wall (N cores worth of work packed into 1 core
    # worth of wall). If workers end up serialized (thermal throttle,
    # single-core, scheduler contention), the sum approximately equals wall.
    if _worker_wall_ms_total:
        _total_worker_ms = sum(_worker_wall_ms_total)
        _pages_phase_ms = int((time.monotonic() - t0) * 1000)
        _efficiency = _total_worker_ms / max(1, _pages_phase_ms)
        _emit(
            "triage",
            f"parallelism: sum(worker)={_total_worker_ms}ms, wall={_pages_phase_ms}ms, "
            f"efficiency={_efficiency:.2f}x "
            f"(ideal={len(_worker_wall_ms_total)}x if fully parallel, "
            f"1.0x = no parallelism)",
        )

    _check_total_timeout("per-page")

    # Merge pages in original order (matters when parallel completes out of order).
    page_pdfs: list[bytes] = [page_pdfs_by_index[i] for i in sorted(_selected_indices)]

    assert len(page_pdfs) == len(_selected_indices), (
        f"page_pdfs has {len(page_pdfs)} entries but {len(_selected_indices)} were selected"
    )

    n_color_discarded = sum(1 for w in warnings_list if "color-detected-in-monochrome-mode" in w)
    if n_color_discarded > 0:
        warnings_list.append(f"force-monochrome-discarded-color-on-{n_color_discarded}-pages")

    # Merge pages.
    _emit(
        "merge_start",
        f"merging {len(page_pdfs)} pages into output PDF",
        total=len(page_pdfs),
    )
    merged = pikepdf.new()
    for page_bytes in page_pdfs:
        src = pikepdf.open(io.BytesIO(page_bytes))
        try:
            merged.pages.extend(src.pages)
        finally:
            src.close()
    out_buf = io.BytesIO()
    merged.save(out_buf, linearize=False, deterministic_id=True)
    output_bytes = out_buf.getvalue()
    _emit(
        "merge_complete",
        f"merge complete: {len(output_bytes):,} bytes "
        f"(ratio {len(input_data) / max(1, len(output_bytes)):.2f}x)",
        ratio=len(input_data) / max(1, len(output_bytes)),
    )

    _check_total_timeout("merge")

    # --- Passthrough: min_ratio floor ---
    # If realized compression ratio is below options.min_ratio, return
    # the ORIGINAL input — a user who set min_ratio=1.5 doesn't want an
    # output bigger than the input. Must fire BEFORE the verifier so we
    # don't waste wall time re-OCRing a shard we're about to discard.
    realized_ratio = len(input_data) / max(1, len(output_bytes))
    if options.min_ratio > 0 and realized_ratio < options.min_ratio:
        wall_ms = int((time.monotonic() - t0) * 1000)
        reason = (
            f"realized ratio {realized_ratio:.2f}x below "
            f"min_ratio={options.min_ratio}x; returning original input"
        )
        return input_data, _build_passthrough_report(
            input_data,
            pages=tri.pages,
            wall_ms=wall_ms,
            reason=reason,
            warning_code="passthrough-ratio-floor",
            correlation_id=correlation_id,
        )

    verifier_result = (
        verifier_agg.skipped_result() if options.skip_verify else verifier_agg.result()
    )
    if verifier_result.status == "fail":
        if options.mode != "fast" and not options.accept_drift:
            summary = verifier_agg.failure_summary()
            failing = list(verifier_result.failing_pages)
            failing_display = (
                f"pages {failing}"
                if len(failing) <= _FAILING_PAGES_INLINE_LIMIT
                else f"{len(failing)} pages ({failing[:5]} ... {failing[-3:]})"
            )
            msg = (
                f"content drift on {failing_display}.\n\n"
                f"{summary}\n\n"
                f"raw metrics: ocr_lev={verifier_result.ocr_levenshtein:.4f}, "
                f"ssim_global={verifier_result.ssim_global:.4f}, "
                f"ssim_tile_min={verifier_result.ssim_min_tile:.4f}, "
                f"digit_multiset_match={verifier_result.digit_multiset_match}, "
                f"color_preserved={verifier_result.color_preserved}\n\n"
                f"to proceed anyway (accepts drift): --mode fast"
            )
            raise ContentDriftError(msg)
        tag = "accept-drift" if options.accept_drift else "fast-mode"
        # Warning codes must stay grep-friendly — no brackets, no spaces.
        # _format_verifier_failing_pages caps at the first 10 ids with a
        # +N suffix for longer lists.
        warnings_list.append(
            f"verifier-fail-{tag}-pages-"
            f"{_format_verifier_failing_pages(verifier_result.failing_pages)}",
        )

    # --- Hashes ---
    input_sha = hashlib.sha256(input_data).hexdigest()
    output_sha = hashlib.sha256(output_bytes).hexdigest()
    try:
        canonical_sha: str | None = canonical_input_sha256(input_data, password=options.password)
    except pikepdf.PdfError:
        canonical_sha = None

    wall_ms = int((time.monotonic() - t0) * 1000)
    ratio = len(input_data) / max(1, len(output_bytes))
    status: Literal["ok", "drift_aborted"] = "ok"
    exit_code = 0

    # Build the audit-sidecar fields (Wave 5 / C2). build_info is cached
    # across compress() calls in this process; correlation_id is whatever
    # the caller passed or a fresh UUID4 via CompressReport's default
    # factory.
    from pdf_smasher.audit import resolve_build_info
    from pdf_smasher.types import _new_correlation_id

    report = CompressReport(
        status=status,
        exit_code=exit_code,
        input_bytes=len(input_data),
        output_bytes=len(output_bytes),
        ratio=ratio,
        pages=len(page_pdfs),
        wall_time_ms=wall_ms,
        engine="mrc",
        engine_version=__engine_version__,
        verifier=verifier_result,
        input_sha256=input_sha,
        output_sha256=output_sha,
        canonical_input_sha256=canonical_sha,
        warnings=tuple(warnings_list),
        strategy_distribution=dict(strategy_counts),
        build_info=resolve_build_info(),
        correlation_id=correlation_id if correlation_id is not None else _new_correlation_id(),
    )
    return output_bytes, report


def compress_stream(
    input_stream: IO[bytes],
    output_stream: IO[bytes],
    options: CompressOptions | None = None,
) -> CompressReport:
    """Streaming compression variant. See ``docs/SPEC.md`` §1.2."""
    data = input_stream.read()
    out, report = compress(data, options=options)
    output_stream.write(out)
    return report
