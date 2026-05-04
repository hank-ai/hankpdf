"""RLIMIT_AS application for Linux + macOS.

RLIMIT_AS (address space) is honored on both. RLIMIT_DATA is too narrow on
Linux because mmap-backed mallocs (which jemalloc + modern glibc use for
large allocations) bypass it. RLIMIT_RSS is a no-op on Linux. RLIMIT_AS
is the cap that actually works.
"""

from __future__ import annotations

import resource


def apply(byte_limit: int) -> None:
    """Set RLIMIT_AS (soft + hard) on the current process."""
    resource.setrlimit(resource.RLIMIT_AS, (byte_limit, byte_limit))
