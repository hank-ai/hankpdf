"""Verify dataclass defaults and basic construction."""

from __future__ import annotations

from pdf_smasher.types import (
    CompressOptions,
    CompressReport,
    TriageReport,
    VerifierResult,
)


def test_compress_options_defaults() -> None:
    opts = CompressOptions()
    assert opts.engine == "mrc"
    assert opts.mode == "standard"
    assert opts.target_bg_dpi == 150
    assert opts.bg_chroma_subsampling == "4:4:4"
    assert opts.legal_codec_profile is False
    assert opts.ocr is True
    assert opts.ocr_language == "eng"
    assert opts.allow_signed_invalidation is False
    assert opts.allow_certified_invalidation is False
    assert opts.max_input_mb == 2000.0
    assert opts.password is None


def test_compress_options_is_frozen() -> None:
    import dataclasses

    import pytest

    opts = CompressOptions()
    with pytest.raises(dataclasses.FrozenInstanceError):
        opts.mode = "safe"  # type: ignore[misc]


def test_verifier_result_construct() -> None:
    v = VerifierResult(
        status="pass",
        ocr_levenshtein=0.004,
        ssim_global=0.975,
        ssim_min_tile=0.94,
        digit_multiset_match=True,
        structural_match=True,
        failing_pages=(),
    )
    assert v.status == "pass"
    assert v.failing_pages == ()


def test_triage_report_construct() -> None:
    t = TriageReport(
        pages=10,
        input_bytes=1000,
        is_encrypted=False,
        is_signed=False,
        is_certified_signature=False,
        is_linearized=False,
        is_tagged=False,
        is_pdf_a=False,
        has_embedded_files=False,
        has_javascript=False,
        has_jbig2_streams=False,
        producer_fingerprint=None,
        classification="proceed",
    )
    assert t.classification == "proceed"
    assert t.notes == ()


def test_compress_report_construct() -> None:
    v = VerifierResult(
        status="pass",
        ocr_levenshtein=0.0,
        ssim_global=1.0,
        ssim_min_tile=1.0,
        digit_multiset_match=True,
        structural_match=True,
    )
    report = CompressReport(
        status="ok",
        exit_code=0,
        input_bytes=100,
        output_bytes=50,
        ratio=2.0,
        pages=1,
        wall_time_ms=123,
        engine="mrc",
        engine_version="0.0.0",
        verifier=v,
        input_sha256="0" * 64,
        output_sha256="1" * 64,
        canonical_input_sha256="2" * 64,
    )
    assert report.schema_version == 1
    assert report.ratio == 2.0
