"""Stable warning codes emitted on the CLI's stderr.

Every CLI stderr line that starts with ``[hankpdf] warning`` includes a
stable bracketed code like ``[W-CHUNKS-EXCEED-CAP]`` so batch scripts that
tee stderr to a log file can grep by code without depending on exact
English wording.

These are distinct from :data:`pdf_smasher.CompressReport.warnings`
(programmatic JSON codes, kebab-case). Stderr warnings happen in the CLI
*after* :func:`compress` returns (the chunk split is a CLI concern, not a
library one), so they never appear in the structured report.

See ``docs/SPEC.md`` §8.5.1 for the canonical list and usage rules.
"""

from __future__ import annotations

from typing import Final, Literal

# The full Literal set — if you add a code, add it here too. The Literal
# makes `emit()` reject typos at type-check time.
CliWarningCode = Literal[
    "W-MAX-OUTPUT-MB-IMAGE-MODE",
    "W-MAX-OUTPUT-MB-STDOUT",
    "W-OUTPUT-FORMAT-EXTENSION-OVERRIDE",
    "W-CHUNKS-EXCEED-CAP",
    "W-STALE-CHUNK-FILES",
    "W-SINGLE-CHUNK-OVERSIZE",
    "W-VERIFIER-SKIPPED",
    "W-VERIFIER-FAILED",
    "W-IMAGE-EXPORT-PARTIAL-FAILURE",
    "W-CHUNK-WRITE-PARTIAL-FAILURE",
]

WARN_PREFIX: Final = "[hankpdf] warning"
ERROR_PREFIX: Final = "[hankpdf] error"


def emit(code: CliWarningCode, message: str) -> str:
    """Build a stable-code stderr warning line. Caller prints it.

    Example: ``emit("W-CHUNKS-EXCEED-CAP", "2 chunks exceed the cap…")``
    returns ``"[hankpdf] warning [W-CHUNKS-EXCEED-CAP]: 2 chunks exceed the cap…"``.

    The bracketed code lets batch scripts grep uniformly:

        grep -F "[W-CHUNKS-EXCEED-CAP]" job.log
    """
    return f"{WARN_PREFIX} [{code}]: {message}"


def emit_error(code: CliWarningCode, message: str) -> str:
    """Build a stable-code stderr ERROR line for partial-write failures.

    Same shape as :func:`emit`, but uses the ``error`` noun-phrase rather
    than ``warning``. Used for ``W-*-PARTIAL-FAILURE`` codes where the
    job already failed — not technically warnings.
    """
    return f"{ERROR_PREFIX} [{code}]: {message}"
