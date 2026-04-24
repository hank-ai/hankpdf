"""Atomic filesystem write helper.

Partial writes through plain ``Path.write_bytes`` leave truncated files at
the final name if interrupted mid-write (SIGINT, disk full, permission
flip). Downstream automation that globs ``{base}_*.pdf`` or
``page_*.jpg`` then silently ingests a partial file; the stale-chunk
regex can't detect truncation within the current chunk index range
(reviewers C + D).

:func:`_atomic_write_bytes` writes to a sibling ``<path>.partial`` first
then uses ``Path.replace()`` (POSIX ``rename(2)`` — atomic on same-
filesystem boundaries) to swing the final name into place. On crash
mid-write the final path never exists; only the ``.partial`` sibling
may remain for the operator to clean up.
"""

from __future__ import annotations

from pathlib import Path

PARTIAL_SUFFIX = ".partial"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    Writes to ``path.with_suffix(path.suffix + PARTIAL_SUFFIX)`` first,
    then :meth:`Path.replace`-es into ``path``. On POSIX, rename(2) is
    atomic across a same-filesystem boundary — the final-named file
    either doesn't exist or is a complete write, never a truncation.

    Caller must ensure ``path.parent`` exists. Kept as a private helper
    (underscore prefix) because the surface is tiny and should stay
    internal — the name is load-bearing for tests that assert we use it.
    """
    tmp = path.with_suffix(path.suffix + PARTIAL_SUFFIX)
    tmp.write_bytes(data)
    tmp.replace(path)
