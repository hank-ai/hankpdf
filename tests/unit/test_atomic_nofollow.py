"""_atomic_write_bytes refuses to follow a pre-placed symlink."""

from __future__ import annotations

import os

import pytest

from hankpdf.utils.atomic import PARTIAL_SUFFIX, _atomic_write_bytes


@pytest.mark.skipif(os.name == "nt", reason="O_NOFOLLOW is POSIX-only")
def test_atomic_write_refuses_symlinked_partial_path(tmp_path) -> None:
    final = tmp_path / "out.pdf"
    partial = tmp_path / f"out.pdf{PARTIAL_SUFFIX}"
    bait = tmp_path / "bait.txt"
    bait.write_text("untouched")
    partial.symlink_to(bait)
    with pytest.raises(OSError):  # noqa: PT011 — O_NOFOLLOW raises generic OSError; the bait check below verifies the cause
        _atomic_write_bytes(final, b"hello")
    assert bait.read_text() == "untouched"


def test_atomic_write_happy_path_overwrites_pre_existing_partial(tmp_path) -> None:
    """Regression: a pre-existing NON-symlink partial gets overwritten cleanly."""
    final = tmp_path / "out.pdf"
    partial = tmp_path / f"out.pdf{PARTIAL_SUFFIX}"
    partial.write_bytes(b"stale")
    _atomic_write_bytes(final, b"fresh")
    assert final.read_bytes() == b"fresh"
    assert not partial.exists()
