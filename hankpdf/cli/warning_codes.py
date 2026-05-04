"""Stable warning codes emitted on the CLI's stderr.

Every CLI stderr line that starts with ``[hankpdf] warning`` includes a
stable bracketed code like ``[W-CHUNKS-EXCEED-CAP]`` so batch scripts that
tee stderr to a log file can grep by code without depending on exact
English wording.

These are distinct from :data:`hankpdf.CompressReport.warnings`
(programmatic JSON codes, kebab-case). Stderr warnings happen in the CLI
*after* :func:`compress` returns (the chunk split is a CLI concern, not a
library one), so they never appear in the structured report.

See ``docs/SPEC.md`` §8.5.1 for the canonical list and usage rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from hankpdf.audit import get_correlation_id
from hankpdf.utils.log import redact_filename

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

# Refusal / failure error codes. These tag the `[hankpdf] error` lines so
# batch log scraping can grep for a specific refusal reason without
# depending on English wording. Exit codes disambiguate them too, but
# log files mixing stdout+stderr from many jobs make grep more useful.
# Source of truth: this Literal. SPEC.md §8.5.1 lists them.
CliErrorCode = Literal[
    "E-INPUT-ENCRYPTED",
    "E-INPUT-SIGNED",
    "E-INPUT-CERTIFIED",
    "E-INPUT-CORRUPT",
    "E-INPUT-MALICIOUS",
    "E-INPUT-OVERSIZE",
    "E-INPUT-DECOMPRESSION-BOMB",
    "E-INPUT-NOT-PDF",
    "E-ENGINE-ERROR",
    "E-VERIFIER-FAIL",
    "E-TIMEOUT-PER-PAGE",
    "E-TIMEOUT-TOTAL",
    "E-OCR-TIMEOUT",
    "E-HOST-RESOURCE",
]

WARN_PREFIX: Final = "[hankpdf] warning"
ERROR_PREFIX: Final = "[hankpdf] error"


def _corr_suffix() -> str:
    """Return the correlation-id tag for stderr lines, or ''.

    Wave 5 / C2: every stderr line includes ``corr=<short-id>`` so an on-
    call can tie a batch log slice back to the structured
    :class:`~hankpdf.types.CompressReport` (which carries the full id).
    When the audit module hasn't set an id yet (library callers skipping
    the CLI entirely), this returns an empty string — the lines still
    work, they just can't be joined post-hoc.
    """
    cid = get_correlation_id()
    if cid is None:
        return ""
    # Short form (first 8 hex chars) for stderr readability; full UUID
    # lives in report.correlation_id for lossless joins.
    return f" corr={cid[:8]}"


def emit(
    code: CliWarningCode,
    message: str,
    *,
    input_name: str | Path | None = None,
) -> str:
    """Build a stable-code stderr warning line. Caller prints it.

    Example: ``emit("W-CHUNKS-EXCEED-CAP", "2 chunks exceed the cap…",
    input_name="in.pdf")`` returns
    ``"[hankpdf] <redacted>: warning [W-CHUNKS-EXCEED-CAP]: 2 chunks exceed the cap…"``.

    The bracketed code lets batch scripts grep uniformly:

        grep -F "[W-CHUNKS-EXCEED-CAP]" job.log

    ``input_name`` (optional) prefixes the redacted filename so a batch
    script teeing all stderr to one log file can tell which input a line
    refers to. Redacted via :func:`hankpdf.utils.log.redact_filename`
    (sha1 prefix + last 8 chars) per THREAT_MODEL.md §5. When None the
    filename prefix is omitted (programmatic stdin inputs, --doctor).
    """
    tagged = f"[{code}]: {message}"
    corr = _corr_suffix()
    if input_name is None:
        return f"{WARN_PREFIX}{corr} {tagged}"
    return f"[hankpdf]{corr} {redact_filename(input_name)}: warning {tagged}"


def emit_error(
    code: CliWarningCode | CliErrorCode,
    message: str,
    *,
    input_name: str | Path | None = None,
) -> str:
    """Build a stable-code stderr ERROR line for partial-write failures
    and refusals.

    Same shape as :func:`emit`, but uses the ``error`` noun-phrase rather
    than ``warning``. Accepts both ``W-*-PARTIAL-FAILURE`` codes (where
    a job was partially completed) and ``E-*`` refusal/timeout codes.
    """
    tagged = f"[{code}]: {message}"
    corr = _corr_suffix()
    if input_name is None:
        return f"{ERROR_PREFIX}{corr} {tagged}"
    return f"[hankpdf]{corr} {redact_filename(input_name)}: error {tagged}"


def emit_refusal(
    code: CliErrorCode,
    reason: str,
    *,
    input_name: str | Path | None = None,
) -> str:
    """Build a stable-code refusal line for input-policy / engine rejects.

    Thin wrapper around :func:`emit_error` that enforces the code is an
    ``E-*`` refusal code (via the narrower CliErrorCode type) and uses
    the word ``refused:`` in the message for backward compatibility with
    scripts that already grep for it (see test_cli_e2e.py).
    """
    return emit_error(code, f"refused: {reason}", input_name=input_name)


def line_prefix(input_name: str | Path | None) -> str:
    """Return the ``[hankpdf] <redacted>:`` prefix for generic summary/log
    lines (those that aren't warnings/errors). Pairs with `WARN_PREFIX`
    / `ERROR_PREFIX` at the structural level — summary lines end up as
    ``[hankpdf] <redacted>: wrote 12 chunks …`` instead of plain
    ``[hankpdf] wrote 12 chunks …``.

    Returns ``"[hankpdf]"`` when ``input_name`` is None (stdin, --doctor).

    Wave 5 / C2: the correlation-id tag is appended when the audit module
    has recorded one for this process so generic summary lines can also
    be joined back to a :class:`CompressReport`.
    """
    corr = _corr_suffix()
    if input_name is None:
        return f"[hankpdf]{corr}"
    return f"[hankpdf]{corr} {redact_filename(input_name)}:"
