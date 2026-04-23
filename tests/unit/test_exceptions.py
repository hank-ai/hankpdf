"""Verify the exception hierarchy matches docs/SPEC.md §1.1."""

from __future__ import annotations

import pytest

from pdf_smasher.exceptions import (
    CertifiedSignatureError,
    CompressError,
    ContentDriftError,
    CorruptPDFError,
    DecompressionBombError,
    EncryptedPDFError,
    EnvironmentError,  # noqa: A004 — HankPDF-specific subclass of CompressError
    MaliciousPDFError,
    OversizeError,
    SignedPDFError,
)


@pytest.mark.parametrize(
    "cls",
    [
        EncryptedPDFError,
        SignedPDFError,
        CertifiedSignatureError,
        MaliciousPDFError,
        ContentDriftError,
        OversizeError,
        DecompressionBombError,
        CorruptPDFError,
        EnvironmentError,
    ],
)
def test_error_is_compress_error_subclass(cls: type) -> None:
    assert issubclass(cls, CompressError)


def test_certified_signature_is_signed_subclass() -> None:
    """Certifying signatures need stricter handling but are a kind of signed PDF."""
    assert issubclass(CertifiedSignatureError, SignedPDFError)


def test_errors_carry_messages() -> None:
    err = EncryptedPDFError("needs password")
    assert str(err) == "needs password"
