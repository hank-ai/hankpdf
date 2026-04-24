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
    assert opts.legal_codec_profile is None
    assert opts.ocr is False  # opt-in: pass --ocr (or ocr=True) for a searchable layer
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
    # Schema v3 bump — see SPEC.md §11. v3 added (Wave 5):
    #   - CompressReport.build_info (BuildInfo | None)
    #   - CompressReport.correlation_id (UUID4 hex)
    # v2 added:
    #   - VerifierResult.status "skipped" literal
    #   - CompressReport.warnings kebab-case codes (e.g. verifier-skipped)
    #   - CompressReport.strategy_distribution populated
    assert report.schema_version == 3
    assert report.ratio == 2.0
    # correlation_id auto-generates via default_factory — shouldn't be empty.
    assert report.correlation_id
    assert len(report.correlation_id) == 32  # UUID4 hex is 32 chars
    # build_info defaults to None when constructed directly (no BuildInfo passed).
    assert report.build_info is None


def test_compress_report_correlation_ids_are_unique() -> None:
    """Two back-to-back constructions must get distinct correlation ids.

    If the default_factory ever gets frozen to a module-level constant
    (or lru_cache'd by accident), two reports in the same process could
    collide — which would defeat the purpose of C2's on-call workflow.
    """
    v = VerifierResult(
        status="pass",
        ocr_levenshtein=0.0,
        ssim_global=1.0,
        ssim_min_tile=1.0,
        digit_multiset_match=True,
        structural_match=True,
    )
    common = {
        "status": "ok",
        "exit_code": 0,
        "input_bytes": 100,
        "output_bytes": 50,
        "ratio": 2.0,
        "pages": 1,
        "wall_time_ms": 123,
        "engine": "mrc",
        "engine_version": "0.0.0",
        "verifier": v,
        "input_sha256": "0" * 64,
        "output_sha256": "1" * 64,
        "canonical_input_sha256": "2" * 64,
    }
    a = CompressReport(**common)
    b = CompressReport(**common)
    assert a.correlation_id != b.correlation_id


def test_build_info_dataclass_round_trip() -> None:
    """BuildInfo is frozen + serializes cleanly via asdict."""
    import dataclasses

    import pytest

    from pdf_smasher.types import BuildInfo

    info = BuildInfo(
        version="1.2.3",
        git_sha="abcdef1",
        build_date="2026-04-23T00:00:00Z",
        jbig2enc_commit="e3fcf02",
        qpdf_version="12.2.0",
        tesseract_version="5.5.0",
        leptonica_version="1.85.0",
        python_version="3.14.4",
        os_platform="debian-trixie",
    )
    assert info.version == "1.2.3"
    # Frozen
    with pytest.raises(dataclasses.FrozenInstanceError):
        info.version = "9.9.9"  # type: ignore[misc]
    # Default for base_image_digest is '?'
    assert info.base_image_digest == "?"
    # Serialization (asdict must yield a JSON-friendly shape)
    as_json_ready = dataclasses.asdict(info)
    assert as_json_ready["version"] == "1.2.3"
