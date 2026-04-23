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


class OcrTimeoutError(CompressError):
    """Tesseract subprocess exceeded the configured per-page timeout.

    Raised by :func:`pdf_smasher.engine.ocr.tesseract_word_boxes` when
    ``pytesseract`` propagates a :class:`subprocess.TimeoutExpired`. The
    subprocess is already killed by the time we see this; the exception
    lets callers distinguish "we killed it" from "tesseract crashed"
    (which surfaces as ``pytesseract.TesseractError``).
    """


class PerPageTimeoutError(CompressError):
    """Per-page worker exceeded ``CompressOptions.per_page_timeout_seconds``.

    Raised by :func:`pdf_smasher.compress` when a ProcessPoolExecutor
    worker's ``future.result(timeout=…)`` hits the per-page budget.
    """


class TotalTimeoutError(CompressError):
    """Job exceeded ``CompressOptions.total_timeout_seconds``.

    Top-level wall-clock watchdog raised when the cumulative elapsed time
    in :func:`pdf_smasher.compress` exceeds the configured total budget.
    """
