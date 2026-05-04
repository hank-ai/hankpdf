"""Unit tests for hankpdf.sandbox.platform_caps."""

from __future__ import annotations

import os
import sys

import pytest

from hankpdf.sandbox.platform_caps import (
    CapsUnavailableError,
    apply_self_memory_cap,
    supported,
)


@pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "Linux-only readback test. macOS Darwin kernel rejects setrlimit("
        "RLIMIT_AS, ...) with EINVAL for any non-infinite value — the "
        "facility is unsupported at the kernel level, not a Python quirk. "
        "Windows uses Job Objects, tested separately in Task 7."
    ),
)
def test_apply_self_memory_cap_unix_sets_rlimit_as() -> None:
    import resource

    pid = os.fork()
    if pid == 0:
        try:
            apply_self_memory_cap(2 * 1024**3)  # 2 GB
            soft, _hard = resource.getrlimit(resource.RLIMIT_AS)
            os._exit(0 if soft == 2 * 1024**3 else 7)
        except Exception:  # pragma: no cover
            os._exit(8)
    else:
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific behavior")
def test_apply_self_memory_cap_darwin_raises_or_noops() -> None:
    """macOS kernel rejects RLIMIT_AS lowering with EINVAL.

    Document the platform reality: callers must catch OSError/ValueError
    when running on macOS or rely on supported() + try/except. This test
    pins the current behavior so future refactors don't silently change it.
    """
    pid = os.fork()
    if pid == 0:
        try:
            apply_self_memory_cap(2 * 1024**3)
        except (OSError, ValueError):
            os._exit(0)  # expected on Darwin
        os._exit(7)  # unexpected success
    else:
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0


def test_supported_returns_true_on_known_platforms() -> None:
    assert supported() is True


def test_apply_self_memory_cap_negative_raises() -> None:
    with pytest.raises(ValueError):
        apply_self_memory_cap(-1)


def test_apply_self_memory_cap_zero_no_op() -> None:
    apply_self_memory_cap(0)
