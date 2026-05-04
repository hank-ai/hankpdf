"""Integration tests for per-page worker memory caps + cooperative shutdown.

These exercise the public surface that Task 8 (v0.3.0) wires up:

- ``hankpdf._init_worker(mem_cap, abort_event)`` — applies the cap inside a
  worker (kernel-level RLIMIT_AS / Job Object) AND stashes the shared
  abort event for cooperative shutdown.
- ``hankpdf.check_abort()`` — worker-side cooperative-abort check, raises
  :class:`MemoryCapExceededError` when the parent watchdog signals.
- ``HostResourceError`` — startup-time refusal when the aggregate envelope
  (per_worker_cap × n_workers) would exceed the host's available RAM.
- ``MemoryCapExceededError`` — runtime cap-kill (kernel SIGKILL or
  cooperative drain).
"""

from __future__ import annotations

import multiprocessing as mp
import sys

import pytest


def _spend(cap_bytes: int, alloc_bytes: int) -> None:
    """Helper run inside a child process for the kernel-cap-kill test.

    Imports are local because the parent test driver runs in pytest's
    process and we don't want side-effectful state leaking into other
    tests via the module-global ``_WORKER_ABORT_EVENT``.
    """
    import multiprocessing as mp_inner

    from hankpdf import _init_worker

    # _init_worker takes (mem_cap, abort_event); test constructs an
    # mp.Event in the same process so the worker has a live event to
    # store on _WORKER_ABORT_EVENT (we don't trigger it in this test;
    # we test the kernel-cap-kill path).
    _init_worker(cap_bytes, mp_inner.Event())
    _ = bytearray(alloc_bytes)


@pytest.mark.skipif(
    sys.platform not in ("linux", "darwin", "win32"),
    reason="memory caps only on supported platforms",
)
def test_worker_killed_when_alloc_exceeds_cap() -> None:
    """Spawn a worker with a 256 MB cap, then have it allocate 512 MB.

    On Linux the kernel kills the process via RLIMIT_AS (exit -9 / 137).
    On macOS RLIMIT_AS is unsupported — the cap call no-ops with a
    [W-CAPS-FAILED] / [W-CAPS-UNAVAILABLE] warning, and the bytearray
    allocation succeeds. We assert ``not is_alive() and exitcode != 0``
    on platforms where the cap fires; on macOS the test is still useful
    as a smoke check that ``_init_worker`` accepts the (cap, event)
    signature without raising.
    """
    cap = 256 * 1024 * 1024  # 256 MB
    over = 512 * 1024 * 1024
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_spend, args=(cap, over))
    p.start()
    p.join(timeout=30)
    assert not p.is_alive()
    if sys.platform == "darwin":
        # macOS rejects RLIMIT_AS; the bytearray allocation succeeds and
        # the worker exits cleanly. This is the expected, documented
        # behavior — the parent's RSS watchdog backstops in production.
        return
    assert p.exitcode != 0


def test_init_worker_accepts_zero_cap_no_op() -> None:
    """mem_cap=0 means 'disabled'; init must not raise.

    Restores ``_WORKER_ABORT_EVENT`` afterward so the parent's serial
    compress() path (which checks the same module-global) doesn't see a
    stale event left from this test.
    """
    import multiprocessing as mp_inner

    import hankpdf
    from hankpdf import _init_worker

    _saved = hankpdf._WORKER_ABORT_EVENT
    try:
        _init_worker(0, mp_inner.Event())
    finally:
        hankpdf._WORKER_ABORT_EVENT = _saved


def test_host_resource_error_when_envelope_too_tight(monkeypatch) -> None:
    """When psutil reports tiny available RAM, compress() raises
    HostResourceError before constructing the executor — distinct from
    MemoryCapExceededError (which means a worker died from cap)."""
    import psutil

    from hankpdf import CompressOptions, compress
    from hankpdf.exceptions import HostResourceError
    from tests.conftest import minimal_pdf_bytes

    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")
    # Make psutil report 64 MB available — far below the 256 MB floor
    # times any reasonable n_workers.
    fake = type("M", (), {"available": 64 * 1024 * 1024})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: fake)

    with pytest.raises(HostResourceError, match="below the 256 MB minimum"):
        compress(
            minimal_pdf_bytes(),
            options=CompressOptions(max_workers=8),
        )


def test_check_abort_raises_when_event_set() -> None:
    """Cooperative shutdown: when the shared abort_event is set,
    check_abort() raises MemoryCapExceededError so the worker exits at
    the next safe-write boundary.

    Cleans up ``_WORKER_ABORT_EVENT`` afterward to avoid polluting other
    tests that share this process (the in-process serial path through
    ``compress()`` reads the same module-global).
    """
    import multiprocessing as mp_inner

    import hankpdf
    from hankpdf import _init_worker, check_abort
    from hankpdf.exceptions import MemoryCapExceededError

    _saved = hankpdf._WORKER_ABORT_EVENT
    try:
        ev = mp_inner.Event()
        _init_worker(0, ev)
        # Initially not set — no raise.
        check_abort()
        ev.set()
        with pytest.raises(MemoryCapExceededError):
            check_abort()
    finally:
        hankpdf._WORKER_ABORT_EVENT = _saved


def _build_compressible_multi_page_pdf(n_pages: int) -> bytes:
    """Build an N-page PDF with real raster content per page so compress()
    routes through the per-page pipeline (not the empty-document
    passthrough). Mirrors the helper in test_parallel.py.
    """
    import io

    import numpy as np
    import pikepdf
    from PIL import Image

    pdf = pikepdf.new()
    for page_i in range(n_pages):
        arr = np.full((2200, 1700, 3), 140, dtype=np.uint8)
        arr[300:1900, 200:1500] = 80
        arr[50:100, 50:400] = [200, 40, 40]
        img = Image.fromarray(arr)
        page_height_pt = 792.0 + page_i
        pdf.add_blank_page(page_size=(612.0, page_height_pt))
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
        page.Contents = pdf.make_stream(
            f"q 612 0 0 {page_height_pt} 0 0 cm /Scan Do Q\n".encode("ascii"),
        )
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


@pytest.mark.integration
def test_watchdog_aborts_running_workers_via_shared_event(monkeypatch):
    """End-to-end smoke test for the schema-v5 observability wiring
    (M1 regression guard).

    Drives compress() through the parallel path (max_workers=2 with
    >= _PARALLEL_MIN_PAGES (4) pages of real raster content). Asserts
    that CompressReport.worker_memory_cap_bytes is populated with the
    expected integer cap from the executor branch — catches the M1
    regression where the dataclass defaults to None and the fields
    silently disappear from JSON output.

    Not a stress test of the cooperative-abort path itself: forcing OOM
    deterministically requires a worker that intentionally allocates,
    which is best covered by the unit-level
    ``test_check_abort_raises_when_event_set``.
    """
    monkeypatch.setenv("HANKPDF_SKIP_ENV_CHECK", "1")

    from hankpdf import CompressOptions, compress

    pdf_in = _build_compressible_multi_page_pdf(n_pages=4)
    # 2 GB cap is comfortable for 4 small JPEG pages — workers stay
    # well under the cap so the watchdog doesn't fire and we get a
    # clean report back to inspect.
    cap_mb = 2048
    _, pool_report = compress(
        pdf_in,
        options=CompressOptions(
            mode="fast", max_workers=2, max_worker_memory_mb=cap_mb,
        ),
    )
    # Pool path actually ran — _run_mem_cap was assigned via
    # _compute_worker_mem_cap. The aggregate-envelope check (70 % of
    # available RAM, divided across n_workers) may clamp the cap below
    # the requested cap_mb on memory-pressured CI runners; assert only
    # that it's positive, which is the M1-regression signal.
    assert pool_report.worker_memory_cap_bytes > 0
    assert pool_report.worker_memory_cap_bytes <= cap_mb * 1024 * 1024
    assert isinstance(pool_report.worker_memory_cap_bytes, int)
    # peak_rss may legitimately be 0 if the watchdog poll (0.5 s
    # cadence) never caught a live worker on a small job. Type-check
    # is the regression guard against the field reverting to None.
    assert isinstance(pool_report.worker_peak_rss_max_bytes, int)
    assert pool_report.worker_peak_rss_max_bytes >= 0
