"""Cross-platform per-process memory cap.

``apply_self_memory_cap(bytes)`` applies a hard memory cap to the
*current* process. Designed to be called from inside a
ProcessPoolExecutor worker via the ``initializer`` hook. When the worker
exceeds the cap, the OS terminates the process; the parent's
``concurrent.futures`` driver surfaces the broken pipe as a normal task
failure that callers can translate into MemoryCapExceededError.

Linux/macOS uses RLIMIT_AS. Windows uses ctypes against kernel32.dll to
self-assign to a Job Object with JOB_OBJECT_LIMIT_PROCESS_MEMORY (Win 8+).
No pywin32 dep.
"""

from __future__ import annotations

import sys


class CapsUnavailableError(RuntimeError):
    """Raised when no memory-cap primitive is available on this OS."""


def supported() -> bool:
    """Return True if apply_self_memory_cap will succeed on this platform."""
    return sys.platform in ("linux", "darwin", "win32")


def apply_self_memory_cap(byte_limit: int) -> None:
    """Apply a per-process memory cap. ``byte_limit=0`` disables (no-op).

    Raises:
        ValueError: byte_limit < 0.
        CapsUnavailableError: this OS doesn't support self-capping.
        OSError: kernel rejected the call.
    """
    if byte_limit < 0:
        msg = f"byte_limit must be >= 0 (got {byte_limit})"
        raise ValueError(msg)
    if byte_limit == 0:
        return  # disabled
    if sys.platform in ("linux", "darwin"):
        from hankpdf.sandbox._apply_unix import apply

        apply(byte_limit)
        return
    if sys.platform == "win32":
        from hankpdf.sandbox._apply_win32 import apply  # added in Task 7

        apply(byte_limit)
        return
    msg = f"no memory-cap primitive available on platform {sys.platform!r}"
    raise CapsUnavailableError(msg)
