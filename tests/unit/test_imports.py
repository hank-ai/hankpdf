"""Phase 0 smoke tests — verify the package imports cleanly."""

from __future__ import annotations


def test_top_level_import() -> None:
    import pdf_smasher

    assert pdf_smasher.__version__


def test_public_symbols_exported() -> None:
    import pdf_smasher

    # Types
    assert hasattr(pdf_smasher, "CompressOptions")
    assert hasattr(pdf_smasher, "CompressReport")
    assert hasattr(pdf_smasher, "VerifierResult")
    assert hasattr(pdf_smasher, "TriageReport")

    # Functions
    assert callable(pdf_smasher.compress)
    assert callable(pdf_smasher.compress_stream)
    assert callable(pdf_smasher.triage)

    # Exceptions
    for name in (
        "CompressError",
        "EncryptedPDFError",
        "SignedPDFError",
        "CertifiedSignatureError",
        "MaliciousPDFError",
        "ContentDriftError",
        "OversizeError",
        "DecompressionBombError",
        "CorruptPDFError",
        "EnvironmentError",
    ):
        assert hasattr(pdf_smasher, name), f"missing exception: {name}"


def test_compress_is_implemented_and_rejects_garbage() -> None:
    """Engine is wired — bogus input must raise CorruptPDFError, not NotImplementedError."""
    import pytest

    import pdf_smasher

    with pytest.raises(pdf_smasher.CorruptPDFError):
        pdf_smasher.triage(b"not a pdf")
    with pytest.raises(pdf_smasher.CorruptPDFError):
        pdf_smasher.compress(b"not a pdf")
