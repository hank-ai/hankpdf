"""Windows-only test: Job Object self-assign actually caps the process."""

from __future__ import annotations

import multiprocessing as mp
import sys

import pytest


def _allocate_then_report(cap_bytes: int, alloc_bytes: int, q: "mp.Queue") -> None:
    from hankpdf.sandbox.platform_caps import apply_self_memory_cap

    try:
        apply_self_memory_cap(cap_bytes)
    except Exception as e:  # pragma: no cover
        q.put(("apply-failed", repr(e)))
        return
    try:
        _buf = bytearray(alloc_bytes)
        q.put(("survived", len(_buf)))
    except (MemoryError, OSError) as e:
        q.put(("memory-error", repr(e)))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
def test_job_object_kills_overspending_worker() -> None:
    cap = 256 * 1024 * 1024  # 256 MB
    over = 512 * 1024 * 1024  # 512 MB
    ctx = mp.get_context("spawn")
    q: "mp.Queue[tuple[str, object]]" = ctx.Queue()
    p = ctx.Process(target=_allocate_then_report, args=(cap, over, q))
    p.start()
    p.join(timeout=30)
    assert not p.is_alive(), "worker outlived the cap"
    assert p.exitcode != 0, f"expected nonzero exit; got {p.exitcode}"
