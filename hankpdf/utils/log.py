"""Log-redaction helpers.

Every log site in HankPDF that references a filename or user-derived value
MUST route through one of these helpers. Docs: ``docs/SPEC.md`` §9.2 and
``docs/KNOWLEDGE.md`` §8.3.

CI lint (Phase 2 T2.x onward) bans ``logger.info(f"...{filename}...")`` and
any log call whose f-string contains ``path``, ``filename``, ``basename``,
``producer``, ``ocr_text``, or ``content``. Use these helpers instead.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

HASH_PREFIX_CHARS = 8
TAIL_CHARS = 8


def redact_filename(name: str | Path) -> str:
    """Redact a filename for safe logging.

    Format: ``sha1(basename)[:8]…basename[-8:]`` — hashes the full basename
    for correlation, shows only the last 8 characters for human recognition.

    >>> redact_filename("patient-records-2026.pdf")  # doctest: +SKIP
    '3a7f4b2c…2026.pdf'
    """
    basename = Path(str(name)).name
    digest = hashlib.sha1(basename.encode("utf-8"), usedforsecurity=False).hexdigest()
    tail = basename[-TAIL_CHARS:] if len(basename) >= TAIL_CHARS else basename
    return f"{digest[:HASH_PREFIX_CHARS]}…{tail}"


def redact_text(text: str, *, limit: int = 0) -> str:
    """Return a non-PHI-bearing summary of a blob of text.

    Never returns the text itself. Returns ``"<redacted:N-chars>"``.
    """
    length = len(text)
    if limit and length > limit:
        length = limit
    return f"<redacted:{length}-chars>"
