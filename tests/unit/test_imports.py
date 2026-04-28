"""Phase 0 smoke tests — verify the package imports cleanly."""

from __future__ import annotations


def test_top_level_import() -> None:
    import hankpdf

    assert hankpdf.__version__


def test_public_symbols_exported() -> None:
    import hankpdf

    # Types
    assert hasattr(hankpdf, "CompressOptions")
    assert hasattr(hankpdf, "CompressReport")
    assert hasattr(hankpdf, "VerifierResult")
    assert hasattr(hankpdf, "TriageReport")

    # Functions
    assert callable(hankpdf.compress)
    assert callable(hankpdf.compress_stream)
    assert callable(hankpdf.triage)

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
        assert hasattr(hankpdf, name), f"missing exception: {name}"


def test_compress_is_implemented_and_rejects_garbage() -> None:
    """Engine is wired — bogus input must raise CorruptPDFError, not NotImplementedError."""
    import pytest

    import hankpdf

    with pytest.raises(hankpdf.CorruptPDFError):
        hankpdf.triage(b"not a pdf")
    with pytest.raises(hankpdf.CorruptPDFError):
        hankpdf.compress(b"not a pdf")
