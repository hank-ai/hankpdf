"""HankPDF exception hierarchy.

This file is the source of truth for the exception tree documented in
``docs/SPEC.md`` §1.1 and §7.1. All errors visible at the public API surface
derive from :class:`CompressError`.
"""

from __future__ import annotations


class CompressError(Exception):
    """Base class for every error HankPDF raises from its public API."""


class EncryptedPDFError(CompressError):
    """Input is encrypted and no password was provided."""


class SignedPDFError(CompressError):
    """Input carries a digital signature; invalidation requires explicit opt-in."""


class CertifiedSignatureError(SignedPDFError):
    """Input carries a certifying signature (``/Perms /DocMDP``).

    Stricter than :class:`SignedPDFError`; requires a separate
    ``--allow-certified-invalidation`` opt-in.
    """


class MaliciousPDFError(CompressError):
    """Input tripped a sandbox resource cap (JBIG2 bomb, xref loop, etc.)."""


class ContentDriftError(CompressError):
    """Verifier detected content drift between input and compressed output."""


class OversizeError(CompressError):
    """Input exceeds configured ``max_input_mb`` or ``max_pages``."""


class DecompressionBombError(CompressError):
    """Input's declared or rendered pixel count exceeds decompression-bomb cap."""


class CorruptPDFError(CompressError):
    """Input is unrecoverable by ``pikepdf`` / ``qpdf``."""


class EnvironmentError(CompressError):  # noqa: A001 — this is our own subclass
    """Environment floor violated (e.g. qpdf older than 11.6.3).

    The ``--doctor`` subcommand provides a full diagnostic report.
    """

    # Populated by ``hankpdf._environment.assert_environment_ready`` with a
    # tuple of :class:`hankpdf._environment.EnvFailure` records describing
    # the floor violations. Typed as ``tuple[object, ...]`` to keep
    # ``exceptions.py`` import-free of the probe module.
    failures: tuple[object, ...] = ()


class OcrTimeoutError(CompressError):
    """Tesseract subprocess exceeded the configured per-page timeout.

    Raised by :func:`hankpdf.engine.ocr.tesseract_word_boxes` when
    ``pytesseract`` propagates a :class:`subprocess.TimeoutExpired`. The
    subprocess is already killed by the time we see this; the exception
    lets callers distinguish "we killed it" from "tesseract crashed"
    (which surfaces as ``pytesseract.TesseractError``).
    """


class PerPageTimeoutError(CompressError):
    """Per-page worker exceeded ``CompressOptions.per_page_timeout_seconds``.

    Raised by :func:`hankpdf.compress` when a ProcessPoolExecutor
    worker's ``future.result(timeout=…)`` hits the per-page budget.
    """


class TotalTimeoutError(CompressError):
    """Job exceeded ``CompressOptions.total_timeout_seconds``.

    Top-level wall-clock watchdog raised when the cumulative elapsed time
    in :func:`hankpdf.compress` exceeds the configured total budget.
    """


class MemoryCapExceededError(CompressError):
    """A per-page worker exceeded its memory cap.

    The kernel-level RLIMIT_AS / Job Object cap kills the worker; the
    parent's psutil RSS watchdog also catches malloc-by-mmap escapes.
    Either way the parent surfaces the failure as this exception so
    callers can distinguish "process died from memory pressure" from
    "process crashed".
    """


class HostResourceError(CompressError):
    """The host has insufficient memory to run with the requested cap.

    Distinct from MemoryCapExceededError (which fires after a worker
    actually died). Raised at startup when the aggregate-envelope check
    determines that ``per_worker_cap × n_workers`` would exceed 70% of
    available host RAM. CLI maps this to exit code 19 (E-HOST-RESOURCE).
    """
