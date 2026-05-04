"""HankPDF — aggressive, safety-first PDF compressor for scanned documents.

Public API surface:

    from hankpdf import compress, triage, CompressOptions, CompressReport

Engine implementation lives in submodules; this module re-exports the stable
contract. See docs/SPEC.md §1 for the full specification.
"""

from __future__ import annotations

import hashlib
import io
import os
import re as _re
import shutil
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import (
    ProcessPoolExecutor,
    ThreadPoolExecutor,
    as_completed,
)
from concurrent.futures import TimeoutError as FuturesTimeoutError
from concurrent.futures.process import BrokenProcessPool
from enum import Enum
from typing import IO, Any, Literal

import pikepdf
import psutil

# Import for side effect: installs PIL.Image.MAX_IMAGE_PIXELS per SECURITY.md
# and docs/THREAT_MODEL.md. Must run before any other module that opens images
# via Pillow, so keep this import early in __init__.
from hankpdf import _pillow_hardening as _pillow_hardening
from hankpdf._limits import MAX_PAGE_AXIS_PT
from hankpdf._version import __engine_version__, __version__
from hankpdf.exceptions import (
    CertifiedSignatureError,
    CompressError,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    EnvironmentError,  # noqa: A004 — part of our public error hierarchy
    HostResourceError,
    MaliciousPDFError,
    MemoryCapExceededError,
    OcrTimeoutError,
    OversizeError,
    PerPageTimeoutError,
    SignedPDFError,
    TotalTimeoutError,
)
from hankpdf.types import (
    BuildInfo,
    CompressOptions,
    CompressReport,
    ProgressEvent,
    TriageReport,
    VerifierResult,
)
from hankpdf.utils.text import format_page_list_short

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
    "HostResourceError",
    "MaliciousPDFError",
    "MemoryCapExceededError",
    "OcrTimeoutError",
    "OversizeError",
    "PerPageTimeoutError",
    "PolicyDecision",
    "ProgressEvent",
    "SignedPDFError",
    "TotalTimeoutError",
    "TriageReport",
    "VerifierResult",
    "__version__",
    "_enforce_input_policy",
    "_init_worker",
    "check_abort",
    "compress",
    "compress_stream",
    "triage",
]


class PolicyDecision(Enum):
    """Outcome of :func:`_enforce_input_policy`.

    ``PROCEED`` — every safety gate passed; caller runs the full pipeline.
    ``PASSTHROUGH_PRESERVE_SIGNATURE`` — input is signed and the user opted
    into ``preserve_signatures``; caller must skip the pipeline and return
    the input bytes verbatim with ``signature_state='passthrough-preserved'``
    so the signature stays valid.

    All other policy violations still raise from the
    :class:`CompressError` hierarchy as before — only the signed-PDF
    passthrough branch needed a non-exceptional return.
    """

    PROCEED = "proceed"
    PASSTHROUGH_PRESERVE_SIGNATURE = "passthrough-preserve-signature"


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

# Per-page worker memory cap constants (Task 8).
# Hard ceiling — even legit huge scans don't justify >16 GB per worker
# (rasterized bombs are already bounded by Pillow MAX_IMAGE_PIXELS).
_HARD_CEILING_BYTES = 16 * 1024**3
# Floor — small inputs still get a generous cap for legitimate raster work.
_FLOOR_BYTES = 8 * 1024**3
# Per-page raster inflation factor over input bytes (300 DPI rasterization).
_INFLATION_FACTOR = 16
# Worker death exit codes that signal kernel-level memory-cap kill.
_KERNEL_CAP_KILL_EXITCODES_UNIX = {-9, 137}  # SIGKILL or 128+9
_STATUS_QUOTA_EXCEEDED = 0xC0000044  # Windows: Job Object kill exit code

# Module-global so worker code can reach it without threading through
# every dataclass. Set ONCE per worker via _init_worker.
_WORKER_ABORT_EVENT: Any = None
# Worker-local boot warnings (e.g., W-CAPS-UNAVAILABLE, W-CAPS-FAILED).
# Drained into the first _PageResult.per_page_warnings tuple emitted by
# this worker so the parent's CompressReport.warnings surfaces them.
_WORKER_BOOT_WARNINGS: list[str] = []
_WORKER_BOOT_WARNINGS_DRAINED: bool = False


_CORRELATION_ID_RE = _re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")


def _validate_correlation_id(cid: str | None) -> None:
    if cid is None:
        return
    if not _CORRELATION_ID_RE.match(cid):
        msg = f"correlation_id must match [A-Za-z0-9._:-]{{1,64}} (got {cid!r})"
        raise ValueError(msg)


def _resolve_worker_count(options: CompressOptions, n_pages: int) -> int:
    """Return the actual number of workers for this run. 1 == serial path."""
    if options.max_workers == 1:
        return 1
    if options.max_workers >= _MIN_EXPLICIT_WORKER_COUNT:
        return min(options.max_workers, n_pages)
    auto = max(1, (os.cpu_count() or 4) - _AUTO_WORKER_RESERVE)
    return min(auto, n_pages)


def _pin_blas_threads() -> None:
    """Pin OMP/BLAS to single-thread per process.

    Pins each worker (and the parent, for consistency) to single-threaded
    native libraries so N workers use exactly N cores, not N * cpu_count cores.

    Without this, Tesseract's OpenMP (and numpy BLAS + OpenCV) each try to
    use every core for themselves. Running multiple Tesseract subprocesses
    in parallel workers then creates N*cpu_count threads competing for
    cpu_count cores — context-switch thrash can fully eat the parallel
    speedup (sometimes making parallel slower than serial on the same box).

    Safe to call from both parent and worker — env-vars only, no caps.
    Idempotent.
    """
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
    os.environ["NUMEXPR_NUM_THREADS"] = "1"


def _init_worker(mem_cap: int, abort_event: Any) -> None:
    """ProcessPoolExecutor initializer (worker-only).

    Pins OMP/BLAS threads, applies per-worker memory cap, and stashes
    the shared cooperative-abort event on a module-global the worker
    code path checks at safe-write boundaries.

    ``mem_cap`` is bytes; ``0`` disables the cap (test escape hatch).
    ``abort_event`` is the shared mp.Event — created from the same
    mp_context as the executor by the parent — that all workers see;
    when the parent's RSS watchdog fires, it sets this event and ALL
    in-flight workers cooperatively drain and exit.
    """
    global _WORKER_ABORT_EVENT
    _WORKER_ABORT_EVENT = abort_event
    _pin_blas_threads()

    if mem_cap > 0:
        # Local import keeps the parent process from paying the
        # platform_caps import cost when no cap is requested.
        from hankpdf.sandbox import (
            CapsUnavailableError,
            apply_self_memory_cap,
        )

        try:
            apply_self_memory_cap(mem_cap)
        except CapsUnavailableError as e:
            sys.stderr.write(f"[W-CAPS-UNAVAILABLE] {e}\n")
            _WORKER_BOOT_WARNINGS.append("W-CAPS-UNAVAILABLE")
        except (OSError, ValueError) as e:
            # macOS rejects setrlimit(RLIMIT_AS, ...) with ValueError
            # ("current limit exceeds maximum limit") — the kernel
            # facility is unsupported, not a Python-level argument bug.
            # Treat the same as a kernel rejection so the worker still
            # boots and the parent's RSS watchdog backstops.
            sys.stderr.write(f"[W-CAPS-FAILED] {e}\n")
            _WORKER_BOOT_WARNINGS.append("W-CAPS-FAILED")


def check_abort() -> None:
    """Worker-side cooperative-abort check.

    Call this at safe-write boundaries inside _process_single_page
    (before each pikepdf.save() chunk emission, before heavy raster
    allocations, before strategy compose calls). Raises
    MemoryCapExceededError if the parent's watchdog has signaled abort.
    """
    if _WORKER_ABORT_EVENT is not None and _WORKER_ABORT_EVENT.is_set():
        msg = "aborted by parent watchdog (host memory pressure)"
        raise MemoryCapExceededError(msg)


def _compute_worker_mem_cap(
    input_size_bytes: int,
    n_workers: int,
    options: CompressOptions,
) -> int:
    """Compute the per-worker memory cap with hard ceiling + aggregate envelope.

    Three constraints, in order of precedence:

    1. Aggregate envelope: cap × n_workers must not exceed 70% of host
       available RAM. We pick the smaller of (per-worker computed cap)
       and (host-available × 0.7 / n_workers). Avoids the 7 × 8 GB =
       56 GB scenario on a 32 GB host.
    2. Hard ceiling: never more than 16 GB per worker, regardless of
       input size. A library caller passing 4 GB of bytes would
       otherwise lift the cap to 64 GB (16 × 4 GB) — defeats the bomb
       defense for that input. The CLI clamps via --max-input-mb=250
       but the library API has no such clamp, hence this absolute cap.
    3. Per-input scaling: max(floor, inflation × input_size). Floor is
       8 GB so trivial inputs still get headroom for legitimate
       rasterization.

    options.max_worker_memory_mb overrides constraints 2 and 3 (caller
    knows their workload); constraint 1 (aggregate envelope) still
    applies as a host-protection floor.
    """
    if options.max_worker_memory_mb is not None:
        # 0 is the documented test escape hatch; bypass all clamps.
        if options.max_worker_memory_mb == 0:
            return 0
        per_worker = int(options.max_worker_memory_mb) * 1024 * 1024
    else:
        per_worker = max(_FLOOR_BYTES, _INFLATION_FACTOR * input_size_bytes)
        per_worker = min(per_worker, _HARD_CEILING_BYTES)

    # Aggregate envelope — protect the host.
    try:
        available = psutil.virtual_memory().available
    except Exception:
        # If psutil can't read /proc on a sealed container, skip the
        # envelope check. The hard ceiling above still applies.
        return per_worker
    aggregate_budget = int(available * 0.7)
    envelope_per_worker = aggregate_budget // max(1, n_workers)
    return min(per_worker, envelope_per_worker)


def _requested_worker_count(options: CompressOptions) -> int:
    """Return the user's requested worker count, *not* clamped by n_pages.

    Used for the host-resource envelope check, which must fire on the
    user's intent (max_workers=8) regardless of how many pages the input
    actually has. ``_resolve_worker_count`` clamps to ``min(N, n_pages)``
    which would mask the host-pressure issue on degenerate inputs.
    """
    if options.max_workers >= _MIN_EXPLICIT_WORKER_COUNT:
        return options.max_workers
    if options.max_workers == 1:
        return 1
    return max(1, (os.cpu_count() or 4) - _AUTO_WORKER_RESERVE)


class _WatchdogState:
    """Watchdog state object — readable by the result-collection loop
    after the watchdog has exited. ``exitcodes`` is populated as workers
    disappear from the executor's process table; this avoids the race
    where ``BrokenProcessPool`` clears ``_processes`` before
    ``_classify_worker_death`` reads it.
    """

    __slots__ = (
        "any_cap_exceeded",
        "exitcodes",
        "peak_rss",
        "stop_event",
        "thread_died",
    )

    def __init__(self) -> None:
        self.any_cap_exceeded: bool = False
        self.thread_died: bool = False
        self.stop_event: threading.Event = threading.Event()
        self.exitcodes: dict[int, int] = {}
        # Highest RSS observed across all workers during this run, in
        # bytes. Updated by the watchdog poll loop; surfaced on
        # CompressReport.worker_peak_rss_max_bytes.
        self.peak_rss: int = 0


def _start_rss_watchdog(
    ex: Any,  # ProcessPoolExecutor — kept Any for the thread-pool escape hatch
    mem_cap: int,
    abort_event: Any,  # mp.Event, shared with all workers
    state: _WatchdogState,
) -> threading.Thread:
    """Poll each worker's RSS; if any exceeds the cap, set the shared
    abort_event so ALL workers cooperatively exit at the next
    safe-write boundary.

    The kernel-level cap (RLIMIT_AS on Unix, Job Object on Windows) is
    the authoritative SIGKILL. This watchdog catches malloc-by-mmap
    escapes from RLIMIT_AS on Linux and provides observability for all
    platforms. It does NOT call psutil.Process.terminate() — that path
    corrupts partial output streams.

    Cost: one psutil call per worker per 500ms. Negligible on idle
    hosts; under heavy load /proc reads can take >10ms each.

    Also proactively snapshots worker exitcodes as workers exit, so the
    result-collection loop can classify deaths even after
    BrokenProcessPool clears the executor's _processes table.
    """
    if mem_cap <= 0:
        # No cap: return a stub thread so the finally-block join is safe.
        t = threading.Thread(target=lambda: None, daemon=True)
        t.start()
        return t

    def _loop() -> None:
        try:
            seen_pids: set[int] = set()
            while not state.stop_event.wait(0.5):
                processes = dict(getattr(ex, "_processes", {}))
                for pid, proc in processes.items():
                    seen_pids.add(pid)
                    # Snapshot exitcode if the worker has died; do this
                    # before the RSS poll so we capture deaths the poll
                    # missed. OVERWRITE only when we have a real exit
                    # code to replace a placeholder.
                    ec = getattr(proc, "exitcode", None)
                    if ec is not None:
                        existing = state.exitcodes.get(pid)
                        if existing is None or existing == -1:
                            state.exitcodes[pid] = ec
                    try:
                        rss = psutil.Process(pid).memory_info().rss
                    except (
                        psutil.NoSuchProcess,
                        psutil.AccessDenied,
                        psutil.ZombieProcess,
                    ):
                        continue
                    # Track peak RSS so CompressReport can surface it
                    # (worker_peak_rss_max_bytes). Done BEFORE the cap
                    # comparison so even pre-overrun samples land in the
                    # high-water mark.
                    state.peak_rss = max(state.peak_rss, rss)
                    if rss > mem_cap:
                        sys.stderr.write(
                            f"[W-MEM-RSS-WATCHDOG] worker pid={pid} "
                            f"RSS={rss} exceeded cap={mem_cap}; "
                            f"requesting cooperative shutdown\n"
                        )
                        # Set the shared event — ALL workers see it and
                        # cooperatively drain at next safe-write boundary.
                        abort_event.set()
                        state.any_cap_exceeded = True
                # Capture exitcodes for workers that left the table.
                for pid in list(seen_pids - set(processes.keys())):
                    if pid not in state.exitcodes:
                        # Best-effort placeholder — table may not have
                        # entries for workers that already cleaned up.
                        state.exitcodes.setdefault(pid, -1)
        except Exception as e:  # never let the watchdog die silently
            state.thread_died = True
            sys.stderr.write(f"[W-WATCHDOG-DIED] {type(e).__name__}: {e}\n")
            raise

    t = threading.Thread(target=_loop, daemon=True, name="hankpdf-rss-watchdog")
    t.start()
    return t


def _classify_worker_death(state: _WatchdogState) -> bool:
    """Return True iff at least one worker died from a memory cap.

    Reads ONLY from the watchdog's snapshot state (state.exitcodes,
    state.any_cap_exceeded) — never from executor._processes, which
    may be cleared by BrokenProcessPool before this is called.
    """
    if state.any_cap_exceeded:
        return True
    for ec in state.exitcodes.values():
        if ec in _KERNEL_CAP_KILL_EXITCODES_UNIX:
            return True
        if sys.platform == "win32" and ec == _STATUS_QUOTA_EXCEEDED:
            return True
    return False


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
    mrc_worthy: bool = True  # False = verbatim-copy fast path; default True for back-compat


@_dc(frozen=True)
class _PageResult:
    """What a worker returns. `page_index` is the original position; the
    parent places results into a dict keyed by this and sorts at merge.
    """

    page_index: int
    composed_bytes: bytes
    strategy_name: str  # one of "text_only" / "photo_only" / "mixed" / "already_optimized"
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
    signature_state: Literal[
        "none",
        "passthrough-preserved",
        "invalidated-allowed",
        "certified-invalidated-allowed",
    ] = "none",
) -> CompressReport:
    """Construct a CompressReport for a passthrough return (input unchanged).

    Used by :func:`compress` when one of the ``CompressOptions`` thresholds
    (min_input_mb, min_ratio) short-circuits the pipeline — the output
    equals the input, verifier is marked "skipped" (nothing to compare),
    and a kebab-case warning code names the specific gate that tripped.
    Also used for the signed-PDF preserve-signatures path
    (``signature_state='passthrough-preserved'``).

    Delegates to ``_VerifierAggregator().skipped_result()`` so there's a
    single source of truth for the fail-closed sentinel policy. Without
    this, the hand-rolled VerifierResult here and the one returned by
    ``skipped_result()`` can drift (ocr_levenshtein=0.0 vs 1.0 — one is
    fail-open, one fail-closed).
    """
    import hashlib

    # Local import to avoid circular import at module load: engine.verifier
    # imports from hankpdf.types, and this module re-exports from types.
    from hankpdf.audit import resolve_build_info
    from hankpdf.engine.verifier import _VerifierAggregator
    from hankpdf.types import _new_correlation_id

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
        signature_state=signature_state,
        signature_invalidated=False,
    )


def _enforce_input_policy(
    tri: TriageReport,
    options: CompressOptions,
    input_data: bytes,
) -> PolicyDecision:
    """Apply every safety gate that compress() enforces on the input.

    Raises the appropriate exception from the :class:`CompressError`
    hierarchy if a refusal gate is tripped. Returns a
    :class:`PolicyDecision` to signal whether the caller should
    ``PROCEED`` with the full pipeline or take the signed-PDF passthrough
    shortcut (``PASSTHROUGH_PRESERVE_SIGNATURE``). Both compress() and
    the CLI's image-export path must route through this so users get the
    same refusal behavior regardless of the chosen output format.
    """
    if tri.classification == "require-password" and options.password is None:
        msg = "input is encrypted; supply CompressOptions.password"
        raise EncryptedPDFError(msg)

    if tri.is_certified_signature and not options.allow_certified_invalidation:
        msg = "input carries a certifying signature; --allow-certified-invalidation required"
        raise CertifiedSignatureError(msg)

    if tri.is_signed and not tri.is_certified_signature:
        if options.preserve_signatures:
            return PolicyDecision.PASSTHROUGH_PRESERVE_SIGNATURE
        if not options.allow_signed_invalidation:
            msg = (
                "input is signed; pass --allow-signed-invalidation to recompress "
                "(invalidates signature) or --preserve-signatures to passthrough "
                "(no compression)"
            )
            raise SignedPDFError(msg)

    if options.max_pages is not None and tri.pages > options.max_pages:
        msg = (
            f"input has {tri.pages} pages; max_pages={options.max_pages} "
            "(default tightened from unlimited; pass --max-pages 100000 or "
            "set CompressOptions(max_pages=None) to relax)"
        )
        raise OversizeError(msg)

    input_mb = len(input_data) / (1024 * 1024)
    if input_mb > options.max_input_mb:
        msg = (
            f"input {input_mb:.1f} MB exceeds max_input_mb={options.max_input_mb} "
            "(default tightened from 2000.0; pass --max-input-mb 2000 to relax)"
        )
        raise OversizeError(msg)

    # Page-dimension bomb guard. Rasterize-time check_render_size catches
    # over-pixel allocations inside the worker, but a 60000x20000 pt page
    # that is empty of image content gets routed to the verbatim/passthrough
    # fast path *before* any worker is dispatched — so the rasterize guard
    # never fires. Refuse at triage time regardless of page content density.
    _enforce_page_axis_cap(input_data, options)

    return PolicyDecision.PROCEED


def _enforce_page_axis_cap(input_data: bytes, options: CompressOptions) -> None:
    """Refuse if any page's MediaBox/CropBox axis exceeds MAX_PAGE_AXIS_PT.

    Walks pikepdf-parsed pages once; the open is cheap because triage has
    already validated the document. Uses CropBox when present (visible
    region) and falls back to MediaBox; either axis exceeding the cap
    triggers a :class:`DecompressionBombError`.
    """
    try:
        with pikepdf.open(io.BytesIO(input_data), password=options.password or "") as pdf:
            for idx, page in enumerate(pdf.pages):
                box = page.obj.get("/CropBox") or page.obj.get("/MediaBox")
                if box is None:
                    continue
                try:
                    coords = [float(v) for v in box]  # type: ignore[attr-defined]
                except TypeError, ValueError:
                    continue
                if len(coords) < 4:
                    continue
                width = abs(coords[2] - coords[0])
                height = abs(coords[3] - coords[1])
                if width > MAX_PAGE_AXIS_PT or height > MAX_PAGE_AXIS_PT:
                    msg = (
                        f"page {idx + 1} declares {width:.0f}x{height:.0f} pt "
                        f"(MediaBox/CropBox); axis cap is {MAX_PAGE_AXIS_PT:.0f} pt "
                        f"(200 in). Refusing as a decompression-bomb candidate."
                    )
                    raise DecompressionBombError(msg)
    except pikepdf.PdfError as e:
        # Triage already accepted the document; if pikepdf can't reopen
        # it here, surface as CorruptPDFError so callers see a structured
        # exit code (13) rather than EXIT_ENGINE_ERROR=30.
        msg = f"unable to inspect page dimensions: {e}"
        raise CorruptPDFError(msg) from e


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
    from hankpdf.engine.background import extract_background
    from hankpdf.engine.compose import compose_mrc_page
    from hankpdf.engine.foreground import extract_foreground, is_effectively_monochrome

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

    from hankpdf.engine.compose import (
        compose_photo_only_page,
        compose_text_only_page,
    )
    from hankpdf.engine.foreground import (
        detect_paper_color,
        extract_foreground,
        is_effectively_monochrome,
    )
    from hankpdf.engine.mask import build_mask
    from hankpdf.engine.ocr import tesseract_word_boxes
    from hankpdf.engine.rasterize import rasterize_page
    from hankpdf.engine.strategy import PageStrategy, classify_page
    from hankpdf.engine.text_layer import add_text_layer
    from hankpdf.engine.verifier import (
        _DEFAULT_TILE_SSIM_FLOOR_SAFE,
        _DEFAULT_TILE_SSIM_FLOOR_STANDARD,
        _page_has_color,
        verify_single_page,
    )

    _worker_t0 = time.monotonic()
    # Cooperative-abort check (Task 8): bail before any work begins.
    check_abort()
    # Drain boot warnings (e.g., W-CAPS-UNAVAILABLE, W-CAPS-FAILED) that
    # the worker accumulated in _init_worker. One worker may process
    # many pages; only include boot warnings on the FIRST page result
    # to avoid duplication. We track this with a worker-local "drained"
    # flag.
    _boot_warnings: tuple[str, ...] = ()
    global _WORKER_BOOT_WARNINGS_DRAINED
    if not _WORKER_BOOT_WARNINGS_DRAINED:
        _boot_warnings = tuple(_WORKER_BOOT_WARNINGS)
        _WORKER_BOOT_WARNINGS_DRAINED = True
    i = winput.page_index
    width_pt, height_pt = winput.page_size
    options = winput.options
    is_safe = winput.is_safe
    lev_ceiling = winput.lev_ceiling
    ssim_floor = winput.ssim_floor
    warnings: list[str] = []

    # ── Per-page MRC gate ────────────────────────────────────────────
    # If the page wasn't MRC-worthy (image-byte-fraction below the
    # threshold), skip the entire pipeline — return the unchanged
    # 1-page slice as the composed bytes, with a trivially-passing
    # verdict (same shape as skip_verify uses). The whole-doc-shortcut
    # in compress() catches the all-pages-verbatim case at the top
    # level; this fast path is for the partial-MRC case (some pages
    # MRC, some pages verbatim) where a worker still gets dispatched.
    if not winput.mrc_worthy:
        # Synthetic verdict invariant: this fast path emits
        # _PageVerdict(lev=1.0, ssim_global=0.0, ssim_tile_min=0.0, ...)
        # — sentinel values, not real measurements. They are SAFE only
        # because the _force_full_pipeline interlock (now in
        # hankpdf.engine.per_page_gate) disables the gate when --verify
        # is on, so verbatim verdicts never feed
        # _VerifierAggregator.merge(). If a future change relaxes the
        # interlock (or adds a new gate-disable path), the invariant
        # below will trip first instead of silently corrupting metrics.
        if not options.skip_verify:
            msg = (
                "verbatim worker fast-path reached with skip_verify=False; "
                "synthetic PageVerdict would pollute _VerifierAggregator. "
                "Check the _force_full_pipeline interlock in "
                "hankpdf.engine.per_page_gate."
            )
            raise AssertionError(msg)
        from hankpdf.engine.verifier import PageVerdict as _PageVerdict

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
            per_page_warnings=_boot_warnings,
            input_bytes=len(winput.input_page_pdf),
            output_bytes=len(winput.input_page_pdf),
            ratio=1.0,
            worker_wall_ms=int((time.monotonic() - _worker_t0) * 1000),
        )

    # --- Rasterize input ---
    check_abort()  # heavy raster allocation — bail if parent watchdog signaled
    raster = rasterize_page(
        winput.input_page_pdf, page_index=0, dpi=winput.source_dpi, password=None
    )

    # ── Text-layer policy (see CompressOptions docstring) ────────────────
    # Defaults: preserve any usable upstream text layer. --ocr fills gaps
    # via Tesseract. --strip-text-layer disables both. --re-ocr forces
    # Tesseract even when native is good.
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
    _has_native_text = bool(_native_text and _native_text.strip())

    # Pre-load native word boxes (cheap) so the quality heuristic + the
    # decision below have data to work with. Skip entirely if the user
    # explicitly opted out via --strip-text-layer or --re-ocr.
    word_boxes: list[Any] = []
    _native_decent = False
    if _has_native_text and not options.strip_text_layer and not options.re_ocr:
        from hankpdf.engine.text_layer import (
            extract_native_word_boxes as _extract_native,
        )
        from hankpdf.engine.text_layer import is_native_text_decent

        _native_boxes = _extract_native(
            winput.input_page_pdf,
            page_index=0,
            raster_width_px=raster.size[0],
            raster_height_px=raster.size[1],
        )
        if _native_boxes:
            _native_decent = is_native_text_decent(_native_boxes)
            if _native_decent:
                word_boxes = _native_boxes
            elif options.ocr:
                # Native exists but is garbage; we'll let the Tesseract path
                # below produce fresh word boxes and ignore _native_boxes.
                warnings.append(f"page-{i + 1}-native-text-quality-poor-using-tesseract")

    # Tesseract input OCR runs when:
    #   - native unusable AND user wants searchability (--ocr OR --re-ocr), OR
    #   - native unusable AND verifier runs (needs comparable input text), OR
    #   - --re-ocr is set (always replace native with Tesseract).
    _need_tesseract_for_text_layer = (
        not options.strip_text_layer
        and not word_boxes
        and (bool(options.ocr) or bool(options.re_ocr))
    )
    _need_tesseract_for_verifier = (not options.skip_verify) and not _has_native_text
    need_input_ocr = _need_tesseract_for_text_layer or _need_tesseract_for_verifier
    _input_ocr_future: Any = None
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

        # Output OCR is also still kicked off below when --verify is on; the
        # ThreadPoolExecutor for that case must exist. Create it lazily here
        # if we skipped the input-OCR submission but the verifier will need
        # an output-OCR future. (When skip_verify is True AND native text
        # supplied word_boxes, no pool at all is needed — saves a thread.)
        if _ocr_pool is None and not options.skip_verify:
            _ocr_pool = ThreadPoolExecutor(max_workers=1)
            _pool2 = _ocr_pool
            _ocr_stack.callback(
                lambda: _pool2.shutdown(wait=False, cancel_futures=True),
            )

        def _await_input_ocr() -> None:
            """Populate word_boxes + ocr_text from the background OCR future.
            No-op if already resolved or not needed."""
            nonlocal word_boxes, ocr_text
            if _input_ocr_future is None or word_boxes:
                return
            word_boxes = _input_ocr_future.result()
            ocr_text = " ".join(b.text for b in word_boxes)

        # Verifier ground-truth: native text wins when present (faithful).
        # Otherwise wait on Tesseract.
        if _has_native_text:
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

        # Defensive: classify_page() never returns ALREADY_OPTIMIZED, and
        # the per-page mrc_worthy gate above already short-circuited any
        # verbatim page before we got here. If we ever land here, something
        # upstream is wrong; keep the AssertionError as a tripwire.
        if strategy == PageStrategy.ALREADY_OPTIMIZED:
            msg = (
                f"page {i + 1}: classify_page returned ALREADY_OPTIMIZED but "
                "compress() has no handler for this value."
            )
            raise AssertionError(msg)

        # --- Strategy dispatch ---
        check_abort()  # bail before strategy compose (allocation-heavy)
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

        # Add a text layer whenever we have word boxes — covers both:
        #  (a) native-preserved (default when input had a usable text layer)
        #  (b) Tesseract output from --ocr / --re-ocr / poor-native fallback
        # --strip-text-layer suppresses the layer entirely.
        if not options.strip_text_layer:
            # If --ocr or --re-ocr triggered Tesseract and native didn't
            # already fill word_boxes, await the input-OCR future now.
            if not word_boxes and _need_tesseract_for_text_layer:
                _await_input_ocr()
            if word_boxes:
                check_abort()  # bail before text-layer pikepdf.save() boundary
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
            from hankpdf.engine.verifier import PageVerdict as _PageVerdict

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

    check_abort()  # last chance to bail before parent merges this result
    return _PageResult(
        page_index=i,
        composed_bytes=composed,
        strategy_name=strategy.name.lower(),
        verdict=verdict,
        per_page_warnings=tuple(warnings) + _boot_warnings,
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
    except Exception:  # noqa: S110 — best-effort native-text probe; any error → fall back to OCR.
        pass
    return fallback_ocr_text


def triage(input_data: bytes, *, password: str | None = None) -> TriageReport:
    """Cheap structural scan. Never decodes image streams. See SPEC.md §4."""
    from hankpdf.engine.triage import triage as _triage

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
    # NEW (Task 3): validate args before env precondition
    _validate_correlation_id(correlation_id)

    # Lazy native-dep boot check (cached for the process lifetime via
    # functools.cache on get_environment_report). Raises EnvironmentError
    # with a friendly install hint if tesseract/qpdf/openjpeg are missing
    # or below their floors. Library callers only pay the subprocess
    # probe cost once, on first compress() call in the process.
    from hankpdf._environment import assert_environment_ready

    assert_environment_ready()

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

    # ── Host-resource envelope check (Task 8) ──────────────────────────
    # Fires BEFORE triage / per-page gate / passthrough / executor setup
    # so a host with insufficient available RAM gets a clean refusal
    # regardless of whether the input would otherwise short-circuit
    # (0-page input, whole-doc passthrough, corrupt-PDF refusal, etc.).
    # Uses the user's *requested* worker count (not clamped to n_pages)
    # so the error reflects user intent.
    #
    # The thread-pool escape hatch (HANKPDF_POOL=thread) deliberately
    # bypasses this check — applying RLIMIT_AS to threads would cap the
    # parent. Tests and library callers that want to disable the check
    # also use ``CompressOptions(max_worker_memory_mb=0)``.
    _pool_kind_for_check = os.environ.get("HANKPDF_POOL", "process").lower()
    if _pool_kind_for_check != "thread":
        _check_n_workers = _requested_worker_count(options)
        _check_mem_cap = _compute_worker_mem_cap(len(input_data), _check_n_workers, options)
        if _check_mem_cap > 0 and _check_mem_cap < (256 * 1024 * 1024):
            _msg = (
                f"computed worker memory cap {_check_mem_cap / 1024**2:.0f} MB is below "
                "the 256 MB minimum (host RAM pressure). Reduce --max-workers or "
                "free memory before retrying."
            )
            raise HostResourceError(_msg)

    # Lazy imports of engine modules so ``from hankpdf import CompressOptions``
    # doesn't pay the startup cost of loading pdfium / OpenCV / Tesseract.
    from hankpdf.engine.canonical import canonical_input_sha256
    from hankpdf.engine.triage import triage as _triage
    from hankpdf.engine.verifier import _VerifierAggregator

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

    decision = _enforce_input_policy(tri, options, input_data)
    if decision is PolicyDecision.PASSTHROUGH_PRESERVE_SIGNATURE:
        # Signed input + preserve_signatures=True → return bytes verbatim
        # so the signature stays valid. No pipeline, no rewrite, no merge.
        wall_ms = int((time.monotonic() - t0) * 1000)
        return input_data, _build_passthrough_report(
            input_data,
            pages=tri.pages,
            wall_ms=wall_ms,
            reason="passthrough-signed: preserve_signatures=True",
            warning_code="passthrough-signed",
            correlation_id=correlation_id,
            signature_state="passthrough-preserved",
        )
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

    # ── Per-page MRC gate ────────────────────────────────────────────
    # Score every page once (cheap pikepdf walk: image_xobject_bytes /
    # page_byte_budget). True = MRC-worthy, False = verbatim copy.
    # If no page meets the threshold AND no flag forces full MRC, the
    # input passes through unchanged at <1s wall. Otherwise the
    # per-page flag is threaded into _WorkerInput and the worker
    # short-circuits the pipeline for verbatim pages.
    #
    # The gate is disabled (every page MRC'd) when:
    #   --re-ocr            → Tesseract on every page; verbatim incompatible.
    #   --strip-text-layer  → no-text-layer output; verbatim preserves it.
    #   --legal-mode        → CCITT G4 archival profile re-encodes every page;
    #                          verbatim copy would defeat the legal codec
    #                          guarantee.
    #   --verify (skip_verify=False) → verbatim pages would feed synthetic
    #                          PageVerdict values into _VerifierAggregator
    #                          and pollute the aggregate ssim/lev/digit
    #                          metrics on partial-passthrough runs.
    from hankpdf.engine.per_page_gate import run_per_page_gate

    _gate = run_per_page_gate(input_data, tri, options)
    _mrc_flags = list(_gate.mrc_worthy)

    if _gate.whole_doc_passthrough:
        # Whole-doc shortcut: every page is verbatim → return input unchanged.
        # compress() returns tuple[bytes, CompressReport]; the unchanged input
        # is the byte-identical first element.
        return input_data, _build_passthrough_report(
            input_data,
            pages=tri.pages,
            wall_ms=int((time.monotonic() - t0) * 1000),
            reason="no page meets the image-content threshold",
            warning_code="passthrough-no-image-content",
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
    _verbatim_pages: set[int] = set()

    _worker_wall_ms_total: list[int] = []

    def _merge_result(pos: int, result: _PageResult) -> None:
        """Accumulate a worker result into parent state + emit page_done."""
        page_pdfs_by_index[result.page_index] = result.composed_bytes
        warnings_list.extend(result.per_page_warnings)
        verifier_agg.merge(result.page_index, result.verdict)
        strategy_counts[result.strategy_name] += 1
        if result.strategy_name == "already_optimized":
            _verbatim_pages.add(result.page_index)
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
            mrc_worthy=_mrc_flags[i],
        )
        for i in _selected_indices
    ]

    n_workers = _resolve_worker_count(options, len(_selected_indices))
    use_pool = n_workers > 1 and len(_selected_indices) >= _PARALLEL_MIN_PAGES

    # Observability hooks for CompressReport.worker_memory_cap_bytes and
    # worker_peak_rss_max_bytes (schema v5). The use_pool branch overwrites
    # these; the serial path leaves them at 0 (no per-worker cap applies
    # when pages run in-process, and the watchdog never spawned).
    _run_mem_cap: int = 0
    _run_wd_state: _WatchdogState | None = None

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
        _pin_blas_threads()  # parent: env-vars only; never apply RLIMIT to ourselves
        _ex_kwargs: dict[str, Any] = {"max_workers": n_workers}
        # mem_cap and shared_abort_event are set in the process-pool branch
        # below; the thread-pool escape hatch (HANKPDF_POOL=thread) skips
        # them. Applying RLIMIT_AS from a thread initializer would cap the
        # parent process itself (workers are threads in the same address
        # space) — incorrect. Tests under HANKPDF_POOL=thread run uncapped.
        mem_cap = 0
        shared_abort_event: Any = None
        if _pool_kind == "thread":
            executor_cls: type = ThreadPoolExecutor
            label = "thread (UNSAFE: pdfium not thread-safe, may crash)"
        else:
            executor_cls = ProcessPoolExecutor
            available = _mp.get_all_start_methods()
            chosen_method = "forkserver" if "forkserver" in available else "spawn"
            mem_cap = _compute_worker_mem_cap(len(input_data), n_workers, options)
            _run_mem_cap = mem_cap
            # 0 is the documented test escape hatch — skip the floor check.
            if mem_cap > 0 and mem_cap < (256 * 1024 * 1024):
                # Less than 256 MB per worker is below the floor for legitimate
                # 300-DPI rasterization. Fail loud rather than crash mid-job.
                # Use HostResourceError (NOT MemoryCapExceededError, which means
                # "a worker died from cap" — no worker has been created yet).
                _msg = (
                    f"computed worker memory cap {mem_cap / 1024**2:.0f} MB is below "
                    "the 256 MB minimum (host RAM pressure). Reduce --max-workers or "
                    "free memory before retrying."
                )
                raise HostResourceError(_msg)
            ctx = _mp.get_context(chosen_method)
            # Single shared abort event — created from the executor's
            # mp_context so the underlying semaphore handle is
            # forkserver/spawn-compatible.
            shared_abort_event = ctx.Event()
            _ex_kwargs["initializer"] = _init_worker
            _ex_kwargs["initargs"] = (mem_cap, shared_abort_event)
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
                            "hankpdf.engine.rasterize",
                            "hankpdf.engine.ocr",
                            "hankpdf.engine.mask",
                            "hankpdf.engine.strategy",
                            "hankpdf.engine.compose",
                            "hankpdf.engine.foreground",
                            "hankpdf.engine.text_layer",
                            "hankpdf.engine.verifier",
                            "hankpdf.engine.background",
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
            # Spawn the parent-side RSS watchdog. mem_cap=0 (test escape
            # hatch or thread pool) returns a stub thread so the join is
            # safe regardless. The watchdog observes worker RSS and sets
            # the shared abort_event on cap-overrun; workers cooperatively
            # exit at the next check_abort() boundary.
            _wd_state = _WatchdogState()
            _run_wd_state = _wd_state
            _watchdog = _start_rss_watchdog(ex, mem_cap, shared_abort_event, _wd_state)
            try:
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
                        except BrokenProcessPool, MemoryCapExceededError:
                            # Race-fix: workers killed faster than the watchdog
                            # poll cadence (500 ms) won't have their exit code
                            # in state.exitcodes yet. Drain whatever's in the
                            # (about-to-clear) process table now so the
                            # classifier has fresh data. OVERWRITE: don't let
                            # setdefault mask a real exitcode behind a placeholder.
                            for _pid, _proc in dict(getattr(ex, "_processes", {})).items():
                                _ec = getattr(_proc, "exitcode", None)
                                if _ec is not None:
                                    _wd_state.exitcodes[_pid] = _ec
                            ex.shutdown(wait=False, cancel_futures=True)
                            if _classify_worker_death(_wd_state):
                                _msg = "per-page worker exceeded memory cap"
                                raise MemoryCapExceededError(_msg) from None
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
            finally:
                _wd_state.stop_event.set()
                _watchdog.join(timeout=2)
                # Liveness check: if the watchdog died, the cap was
                # effectively unenforced for at least part of the run.
                # Surface this so users can re-run with --max-workers
                # reduced.
                if _wd_state.thread_died:
                    warnings_list.append("W-WATCHDOG-DIED")
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
    from hankpdf.audit import resolve_build_info
    from hankpdf.types import _new_correlation_id

    # If some-but-not-all pages were verbatim, emit an aggregate warning
    # so consumers see in the report that the gate fired on some pages.
    # Dash separator (not colon) for grammar consistency with the existing
    # `verifier-fail-...-pages-...` pattern. The actual indices are on
    # report.pages_skipped_verbatim; the count here is for log-grep.
    if _verbatim_pages and len(_verbatim_pages) < tri.pages:
        warnings_list.append(f"pages-skipped-verbatim-{len(_verbatim_pages)}")

    # Signature outcome — derived from triage + the opt-in flags. The
    # passthrough-preserve case never reaches this codepath (it returns
    # early upstream), so the only options here are the two
    # invalidation paths or "no signature in the input at all".
    _sig_state: Literal[
        "none",
        "passthrough-preserved",
        "invalidated-allowed",
        "certified-invalidated-allowed",
    ]
    if tri.is_certified_signature and options.allow_certified_invalidation:
        _sig_state = "certified-invalidated-allowed"
        _sig_invalidated = True
    elif tri.is_signed and options.allow_signed_invalidation:
        _sig_state = "invalidated-allowed"
        _sig_invalidated = True
    else:
        _sig_state = "none"
        _sig_invalidated = False

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
        pages_skipped_verbatim=tuple(sorted(_verbatim_pages)),
        strategy_distribution=dict(strategy_counts),
        build_info=resolve_build_info(),
        correlation_id=correlation_id if correlation_id is not None else _new_correlation_id(),
        signature_state=_sig_state,
        signature_invalidated=_sig_invalidated,
        # Schema v5 observability — see SPEC.md §11. _run_mem_cap is the
        # cap actually applied (0 when caps disabled / serial path /
        # thread pool). _run_wd_state.peak_rss is the high-water mark
        # observed by the parent-side watchdog (0 when no watchdog ran).
        worker_memory_cap_bytes=_run_mem_cap,
        worker_peak_rss_max_bytes=_run_wd_state.peak_rss if _run_wd_state else 0,
    )
    return output_bytes, report


def compress_stream(
    input_stream: IO[bytes],
    output_stream: IO[bytes],
    options: CompressOptions | None = None,
    *,
    correlation_id: str | None = None,
) -> CompressReport:
    """Streaming compression variant. See ``docs/SPEC.md`` §1.2.

    ``correlation_id`` (optional, ``[A-Za-z0-9._:-]{1,64}``) is stamped
    onto the returned :class:`CompressReport`. Pass the same id your
    upstream logger prefixes its lines with so the report can be
    joined back to the calling system's log slice. If omitted, a fresh
    UUID4 hex is generated.
    """
    data = input_stream.read()
    out, report = compress(data, options=options, correlation_id=correlation_id)
    output_stream.write(out)
    return report
