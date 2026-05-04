"""Sandbox primitives for per-process resource caps.

Public surface:

- :func:`apply_self_memory_cap` — apply a memory cap to the current
  process (called from inside ProcessPoolExecutor workers).
- :class:`CapsUnavailableError` — raised when the platform has no
  primitive available.
- :func:`supported` — quick boolean for platform support.
"""

from __future__ import annotations

from hankpdf.sandbox.platform_caps import (
    CapsUnavailableError,
    apply_self_memory_cap,
    supported,
)

__all__ = ["CapsUnavailableError", "apply_self_memory_cap", "supported"]
